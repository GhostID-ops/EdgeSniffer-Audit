use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub const PARITY_SCHEMA_VERSION: &str = "teacher-impulse-parity-v1";
pub const PARITY_CODE_HASH: &str = "teacher-impulse-parity-core-20260826-a";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum DecisionStatus {
    Accepted,
    Rejected,
    Unavailable,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ProviderSource {
    ShredStream,
    Yellowstone,
    RabbitStream,
    Rpc,
    Replay,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum Venue {
    PumpFun,
    PumpSwap,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum MayhemStatus {
    Clean,
    HardBlock,
    Unavailable,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProviderEvidence {
    pub source: ProviderSource,
    pub raw_record_identity: String,
    pub raw_record_hash: String,
    pub source_slot: Option<u64>,
    pub source_transaction_index: Option<u32>,
    pub provider_sequence: Option<String>,
    pub local_receive_ts_ms: u64,
    pub decode_complete_ts_ms: Option<u64>,
    pub candidate_ready_ts_ms: Option<u64>,
    pub transaction_signature: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CandidateFeatures {
    pub mint: String,
    pub token_program: Option<String>,
    pub source_ix_data_base64: Option<String>,
    pub creation_proven: Option<bool>,
    pub creation_age_ms: Option<u64>,
    pub market_cap_usd: Option<f64>,
    pub net_buy_sol_60: Option<f64>,
    pub buy_count_100ms: Option<u32>,
    pub buy_count_250ms: Option<u32>,
    pub buy_count_500ms: Option<u32>,
    pub buy_count_1000ms: Option<u32>,
    pub buy_count_60: Option<u32>,
    pub sell_pressure_60: Option<f64>,
    pub dominant_buy_share_60: Option<f64>,
    pub unique_wallets_60: Option<u32>,
    pub unique_per_buy_60: Option<f64>,
    pub first_buyer_quote_share_bps: Option<u32>,
    pub largest_wallet_quote_share_bps: Option<u32>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CanonicalState {
    pub venue: Venue,
    pub source: ProviderSource,
    pub state_identity: String,
    pub state_hash: String,
    pub observed_at_ms: u64,
    pub source_slot: Option<u64>,
    pub market_cap_usd: Option<f64>,
    pub curve_complete: Option<bool>,
    pub unavailable_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MayhemEvidence {
    pub status: MayhemStatus,
    pub reason: Option<String>,
    pub checked_at_ms: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExecutionModel {
    pub position_size_lamports: u64,
    pub priority_fee_lamports: u64,
    pub jito_tip_lamports: u64,
    pub other_tip_lamports: u64,
    pub slippage_bps: u64,
    pub expected_tokens_out_raw: Option<u128>,
    pub min_tokens_out_raw: Option<u128>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct DecisionInput {
    pub schema_version: String,
    pub strategy: String,
    pub provider: ProviderEvidence,
    pub candidate: CandidateFeatures,
    pub canonical_state: Option<CanonicalState>,
    pub mayhem: MayhemEvidence,
    pub execution: ExecutionModel,
    #[serde(default)]
    pub extra_decision_fields: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct StrategyConfig {
    pub strategy: String,
    pub config_version: String,
    pub require_creation_proven: bool,
    pub require_canonical_state: bool,
    pub min_market_cap_usd: Option<f64>,
    pub max_market_cap_usd: Option<f64>,
    pub max_creation_age_ms: Option<u64>,
    pub min_net_buy_sol_60: Option<f64>,
    pub max_sell_pressure_60: Option<f64>,
    pub max_dominant_buy_share_60: Option<f64>,
    pub min_unique_wallets_60: Option<u32>,
    pub max_first_buyer_quote_share_bps: Option<u32>,
    pub round_trip_cost_bps: u64,
}

impl Default for StrategyConfig {
    fn default() -> Self {
        Self {
            strategy: PARITY_SCHEMA_VERSION.to_string(),
            config_version: "teacher-impulse-parity-v1-default-20260826".to_string(),
            require_creation_proven: true,
            require_canonical_state: true,
            min_market_cap_usd: Some(1_500.0),
            max_market_cap_usd: Some(6_000.0),
            max_creation_age_ms: Some(2_500),
            min_net_buy_sol_60: Some(1.0),
            max_sell_pressure_60: Some(0.25),
            max_dominant_buy_share_60: Some(0.65),
            min_unique_wallets_60: Some(2),
            max_first_buyer_quote_share_bps: Some(8_000),
            round_trip_cost_bps: 250,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExpectedEntry {
    pub venue: Venue,
    pub market_cap_usd: f64,
    pub position_size_lamports: u64,
    pub expected_tokens_out_raw: Option<u128>,
    pub min_tokens_out_raw: Option<u128>,
    pub slippage_bps: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct FeeModel {
    pub priority_fee_lamports: u64,
    pub jito_tip_lamports: u64,
    pub other_tip_lamports: u64,
    pub round_trip_cost_bps: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ObservedExecution {
    pub submitted_signature: Option<String>,
    pub submitted_at_ms: Option<u64>,
    pub landed_at_ms: Option<u64>,
    pub first_inventory_proof_at_ms: Option<u64>,
    pub first_inventory_source: Option<String>,
    pub exit_signature: Option<String>,
    pub exit_reason: Option<String>,
    pub gross_result_lamports: Option<i64>,
    pub net_result_lamports: Option<i64>,
    pub discrepancies: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct DecisionReceipt {
    pub schema_version: String,
    pub strategy: String,
    pub config_hash: String,
    pub code_hash: String,
    pub input_hash: String,
    pub deterministic_event_id: String,
    pub status: DecisionStatus,
    pub reason_codes: Vec<String>,
    pub unavailable_fields: Vec<String>,
    pub source_slot_role: String,
    pub source_slot: Option<u64>,
    pub raw_record_identity: String,
    pub mint: String,
    pub calculated_market_cap_usd: Option<f64>,
    pub expected_entry: Option<ExpectedEntry>,
    pub fee_model: FeeModel,
    pub observed_execution: ObservedExecution,
    pub receipt_hash: String,
}

pub fn evaluate_decision(input: &DecisionInput, config: &StrategyConfig) -> DecisionReceipt {
    let input_hash = stable_hash(input);
    let config_hash = stable_hash(config);
    let mut status = DecisionStatus::Accepted;
    let mut reason_codes = Vec::new();
    let mut unavailable_fields = Vec::new();

    if input.schema_version != PARITY_SCHEMA_VERSION {
        reject(&mut status, &mut reason_codes, "schema_version_mismatch");
    }
    if input.strategy != config.strategy || input.strategy != PARITY_SCHEMA_VERSION {
        reject(&mut status, &mut reason_codes, "strategy_mismatch");
    }
    if input.provider.raw_record_hash.trim().is_empty() {
        unavailable(
            &mut status,
            &mut reason_codes,
            &mut unavailable_fields,
            "provider.raw_record_hash",
            "raw_record_hash_missing",
        );
    }
    if let Some(decoded) = input.provider.decode_complete_ts_ms {
        if decoded < input.provider.local_receive_ts_ms {
            reject(&mut status, &mut reason_codes, "decode_before_receive");
        }
    }
    if let Some(ready) = input.provider.candidate_ready_ts_ms {
        if ready < input.provider.local_receive_ts_ms {
            reject(
                &mut status,
                &mut reason_codes,
                "candidate_ready_before_receive",
            );
        }
    }

    match input.mayhem.status {
        MayhemStatus::Clean => {}
        MayhemStatus::HardBlock => reject(&mut status, &mut reason_codes, "mayhem_hard_block"),
        MayhemStatus::Unavailable | MayhemStatus::Unknown => unavailable(
            &mut status,
            &mut reason_codes,
            &mut unavailable_fields,
            "mayhem.status",
            "mayhem_fail_closed",
        ),
    }
    if let (Some(checked_at), Some(candidate_ready)) = (
        input.mayhem.checked_at_ms,
        input.provider.candidate_ready_ts_ms,
    ) {
        if checked_at > candidate_ready {
            unavailable(
                &mut status,
                &mut reason_codes,
                &mut unavailable_fields,
                "mayhem.checked_at_ms",
                "mayhem_check_after_decision",
            );
        }
    }

    if config.require_creation_proven && input.candidate.creation_proven != Some(true) {
        unavailable(
            &mut status,
            &mut reason_codes,
            &mut unavailable_fields,
            "candidate.creation_proven",
            "creation_unproven",
        );
    }

    let canonical_mc = input
        .canonical_state
        .as_ref()
        .and_then(|state| state.market_cap_usd);
    let candidate_mc = input.candidate.market_cap_usd;
    let calculated_market_cap_usd = canonical_mc.or(candidate_mc);

    if config.require_canonical_state {
        match input.canonical_state.as_ref() {
            Some(state) if state.unavailable_reason.is_some() => unavailable(
                &mut status,
                &mut reason_codes,
                &mut unavailable_fields,
                "canonical_state",
                "canonical_state_unavailable",
            ),
            Some(state) if state.curve_complete == Some(true) => {
                reject(&mut status, &mut reason_codes, "curve_complete")
            }
            Some(_) => {}
            None => unavailable(
                &mut status,
                &mut reason_codes,
                &mut unavailable_fields,
                "canonical_state",
                "canonical_state_missing",
            ),
        }
    }
    if let (Some(state), Some(candidate_ready)) = (
        input.canonical_state.as_ref(),
        input.provider.candidate_ready_ts_ms,
    ) {
        if state.observed_at_ms > candidate_ready {
            unavailable(
                &mut status,
                &mut reason_codes,
                &mut unavailable_fields,
                "canonical_state.observed_at_ms",
                "canonical_state_after_decision",
            );
        }
    }

    let Some(mc) = calculated_market_cap_usd else {
        unavailable(
            &mut status,
            &mut reason_codes,
            &mut unavailable_fields,
            "market_cap_usd",
            "market_cap_missing",
        );
        return finish_receipt(
            input,
            config,
            input_hash,
            config_hash,
            status,
            reason_codes,
            unavailable_fields,
            None,
        );
    };

    if let Some(min) = config.min_market_cap_usd {
        if mc < min {
            reject(&mut status, &mut reason_codes, "market_cap_below_min");
        }
    }
    if let Some(max) = config.max_market_cap_usd {
        if mc > max {
            reject(&mut status, &mut reason_codes, "market_cap_above_max");
        }
    }
    require_u64_max(
        input.candidate.creation_age_ms,
        config.max_creation_age_ms,
        &mut status,
        &mut reason_codes,
        &mut unavailable_fields,
        "candidate.creation_age_ms",
        "creation_age_missing",
        "creation_age_above_max",
    );
    require_f64_min(
        input.candidate.net_buy_sol_60,
        config.min_net_buy_sol_60,
        &mut status,
        &mut reason_codes,
        &mut unavailable_fields,
        "candidate.net_buy_sol_60",
        "net_buy_missing",
        "net_buy_below_min",
    );
    require_f64_max(
        input.candidate.sell_pressure_60,
        config.max_sell_pressure_60,
        &mut status,
        &mut reason_codes,
        &mut unavailable_fields,
        "candidate.sell_pressure_60",
        "sell_pressure_missing",
        "sell_pressure_above_max",
    );
    require_f64_max(
        input.candidate.dominant_buy_share_60,
        config.max_dominant_buy_share_60,
        &mut status,
        &mut reason_codes,
        &mut unavailable_fields,
        "candidate.dominant_buy_share_60",
        "dominance_missing",
        "dominance_above_max",
    );
    require_u32_min(
        input.candidate.unique_wallets_60,
        config.min_unique_wallets_60,
        &mut status,
        &mut reason_codes,
        &mut unavailable_fields,
        "candidate.unique_wallets_60",
        "unique_wallets_missing",
        "unique_wallets_below_min",
    );
    require_u32_max(
        input.candidate.first_buyer_quote_share_bps,
        config.max_first_buyer_quote_share_bps,
        &mut status,
        &mut reason_codes,
        &mut unavailable_fields,
        "candidate.first_buyer_quote_share_bps",
        "first_buyer_share_missing",
        "first_buyer_share_above_max",
    );

    match (
        input.execution.expected_tokens_out_raw,
        input.execution.min_tokens_out_raw,
    ) {
        (Some(expected), Some(min_out)) if min_out <= expected => {}
        (Some(_), Some(_)) => reject(
            &mut status,
            &mut reason_codes,
            "min_tokens_out_above_expected",
        ),
        (None, _) => unavailable(
            &mut status,
            &mut reason_codes,
            &mut unavailable_fields,
            "execution.expected_tokens_out_raw",
            "expected_tokens_out_missing",
        ),
        (_, None) => unavailable(
            &mut status,
            &mut reason_codes,
            &mut unavailable_fields,
            "execution.min_tokens_out_raw",
            "min_tokens_out_missing",
        ),
    }

    let expected_entry = if status == DecisionStatus::Accepted {
        Some(ExpectedEntry {
            venue: input
                .canonical_state
                .as_ref()
                .map(|state| state.venue.clone())
                .unwrap_or(Venue::Unknown),
            market_cap_usd: mc,
            position_size_lamports: input.execution.position_size_lamports,
            expected_tokens_out_raw: input.execution.expected_tokens_out_raw,
            min_tokens_out_raw: input.execution.min_tokens_out_raw,
            slippage_bps: input.execution.slippage_bps,
        })
    } else {
        None
    };

    finish_receipt(
        input,
        config,
        input_hash,
        config_hash,
        status,
        reason_codes,
        unavailable_fields,
        expected_entry,
    )
}

fn finish_receipt(
    input: &DecisionInput,
    config: &StrategyConfig,
    input_hash: String,
    config_hash: String,
    status: DecisionStatus,
    reason_codes: Vec<String>,
    unavailable_fields: Vec<String>,
    expected_entry: Option<ExpectedEntry>,
) -> DecisionReceipt {
    let deterministic_event_id = stable_hash(&json!({
        "strategy": input.strategy,
        "raw_record_hash": input.provider.raw_record_hash,
        "mint": input.candidate.mint,
        "candidate_ready_ts_ms": input.provider.candidate_ready_ts_ms,
    }));
    let mut receipt = DecisionReceipt {
        schema_version: PARITY_SCHEMA_VERSION.to_string(),
        strategy: input.strategy.clone(),
        config_hash,
        code_hash: PARITY_CODE_HASH.to_string(),
        input_hash,
        deterministic_event_id,
        status,
        reason_codes,
        unavailable_fields,
        source_slot_role: "grouping_only_not_ordering_or_inventory_proof".to_string(),
        source_slot: input.provider.source_slot,
        raw_record_identity: input.provider.raw_record_identity.clone(),
        mint: input.candidate.mint.clone(),
        calculated_market_cap_usd: input
            .canonical_state
            .as_ref()
            .and_then(|state| state.market_cap_usd)
            .or(input.candidate.market_cap_usd),
        expected_entry,
        fee_model: FeeModel {
            priority_fee_lamports: input.execution.priority_fee_lamports,
            jito_tip_lamports: input.execution.jito_tip_lamports,
            other_tip_lamports: input.execution.other_tip_lamports,
            round_trip_cost_bps: config.round_trip_cost_bps,
        },
        observed_execution: ObservedExecution {
            submitted_signature: None,
            submitted_at_ms: None,
            landed_at_ms: None,
            first_inventory_proof_at_ms: None,
            first_inventory_source: None,
            exit_signature: None,
            exit_reason: None,
            gross_result_lamports: None,
            net_result_lamports: None,
            discrepancies: Vec::new(),
        },
        receipt_hash: String::new(),
    };
    receipt.receipt_hash = stable_hash(&receipt_without_hash(&receipt));
    receipt
}

fn receipt_without_hash(receipt: &DecisionReceipt) -> Value {
    let mut value = serde_json::to_value(receipt).expect("receipt serializes");
    if let Value::Object(ref mut obj) = value {
        obj.insert("receipt_hash".to_string(), Value::String(String::new()));
    }
    value
}

pub fn stable_hash<T: Serialize>(value: &T) -> String {
    let bytes = serde_json::to_vec(value).expect("stable hash value serializes");
    let digest = Sha256::digest(bytes);
    format!("{digest:x}")
}

fn reject(status: &mut DecisionStatus, reason_codes: &mut Vec<String>, reason: &str) {
    if *status != DecisionStatus::Unavailable {
        *status = DecisionStatus::Rejected;
    }
    push_reason(reason_codes, reason);
}

fn unavailable(
    status: &mut DecisionStatus,
    reason_codes: &mut Vec<String>,
    unavailable_fields: &mut Vec<String>,
    field: &str,
    reason: &str,
) {
    *status = DecisionStatus::Unavailable;
    push_reason(reason_codes, reason);
    push_reason(unavailable_fields, field);
}

fn push_reason(values: &mut Vec<String>, value: &str) {
    if !values.iter().any(|item| item == value) {
        values.push(value.to_string());
    }
}

fn require_f64_min(
    value: Option<f64>,
    min: Option<f64>,
    status: &mut DecisionStatus,
    reason_codes: &mut Vec<String>,
    unavailable_fields: &mut Vec<String>,
    field: &str,
    missing_reason: &str,
    reject_reason: &str,
) {
    if let Some(min) = min {
        match value {
            Some(value) if value >= min => {}
            Some(_) => reject(status, reason_codes, reject_reason),
            None => unavailable(
                status,
                reason_codes,
                unavailable_fields,
                field,
                missing_reason,
            ),
        }
    }
}

fn require_f64_max(
    value: Option<f64>,
    max: Option<f64>,
    status: &mut DecisionStatus,
    reason_codes: &mut Vec<String>,
    unavailable_fields: &mut Vec<String>,
    field: &str,
    missing_reason: &str,
    reject_reason: &str,
) {
    if let Some(max) = max {
        match value {
            Some(value) if value <= max => {}
            Some(_) => reject(status, reason_codes, reject_reason),
            None => unavailable(
                status,
                reason_codes,
                unavailable_fields,
                field,
                missing_reason,
            ),
        }
    }
}

fn require_u64_max(
    value: Option<u64>,
    max: Option<u64>,
    status: &mut DecisionStatus,
    reason_codes: &mut Vec<String>,
    unavailable_fields: &mut Vec<String>,
    field: &str,
    missing_reason: &str,
    reject_reason: &str,
) {
    if let Some(max) = max {
        match value {
            Some(value) if value <= max => {}
            Some(_) => reject(status, reason_codes, reject_reason),
            None => unavailable(
                status,
                reason_codes,
                unavailable_fields,
                field,
                missing_reason,
            ),
        }
    }
}

fn require_u32_min(
    value: Option<u32>,
    min: Option<u32>,
    status: &mut DecisionStatus,
    reason_codes: &mut Vec<String>,
    unavailable_fields: &mut Vec<String>,
    field: &str,
    missing_reason: &str,
    reject_reason: &str,
) {
    if let Some(min) = min {
        match value {
            Some(value) if value >= min => {}
            Some(_) => reject(status, reason_codes, reject_reason),
            None => unavailable(
                status,
                reason_codes,
                unavailable_fields,
                field,
                missing_reason,
            ),
        }
    }
}

fn require_u32_max(
    value: Option<u32>,
    max: Option<u32>,
    status: &mut DecisionStatus,
    reason_codes: &mut Vec<String>,
    unavailable_fields: &mut Vec<String>,
    field: &str,
    missing_reason: &str,
    reject_reason: &str,
) {
    if let Some(max) = max {
        match value {
            Some(value) if value <= max => {}
            Some(_) => reject(status, reason_codes, reject_reason),
            None => unavailable(
                status,
                reason_codes,
                unavailable_fields,
                field,
                missing_reason,
            ),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn accepted_input() -> DecisionInput {
        DecisionInput {
            schema_version: PARITY_SCHEMA_VERSION.to_string(),
            strategy: PARITY_SCHEMA_VERSION.to_string(),
            provider: ProviderEvidence {
                source: ProviderSource::ShredStream,
                raw_record_identity: "slot:1:index:2:sig:abc".to_string(),
                raw_record_hash: "rawhashabc".to_string(),
                source_slot: Some(1),
                source_transaction_index: Some(2),
                provider_sequence: Some("seq-1".to_string()),
                local_receive_ts_ms: 1_000,
                decode_complete_ts_ms: Some(1_004),
                candidate_ready_ts_ms: Some(1_008),
                transaction_signature: Some("abc".to_string()),
            },
            candidate: CandidateFeatures {
                mint: "Mint111111111111111111111111111111111111111".to_string(),
                token_program: Some("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA".to_string()),
                source_ix_data_base64: Some("AQID".to_string()),
                creation_proven: Some(true),
                creation_age_ms: Some(420),
                market_cap_usd: Some(3_100.0),
                net_buy_sol_60: Some(8.5),
                buy_count_100ms: Some(2),
                buy_count_250ms: Some(3),
                buy_count_500ms: Some(5),
                buy_count_1000ms: Some(7),
                buy_count_60: Some(7),
                sell_pressure_60: Some(0.0),
                dominant_buy_share_60: Some(0.32),
                unique_wallets_60: Some(6),
                unique_per_buy_60: Some(0.86),
                first_buyer_quote_share_bps: Some(3_200),
                largest_wallet_quote_share_bps: Some(3_200),
            },
            canonical_state: Some(CanonicalState {
                venue: Venue::PumpFun,
                source: ProviderSource::Yellowstone,
                state_identity: "curve:abc".to_string(),
                state_hash: "statehashabc".to_string(),
                observed_at_ms: 1_006,
                source_slot: Some(1),
                market_cap_usd: Some(3_120.0),
                curve_complete: Some(false),
                unavailable_reason: None,
            }),
            mayhem: MayhemEvidence {
                status: MayhemStatus::Clean,
                reason: None,
                checked_at_ms: Some(1_006),
            },
            execution: ExecutionModel {
                position_size_lamports: 1_000_000,
                priority_fee_lamports: 5_000,
                jito_tip_lamports: 10_000,
                other_tip_lamports: 0,
                slippage_bps: 500,
                expected_tokens_out_raw: Some(10_000_000_000),
                min_tokens_out_raw: Some(9_500_000_000),
            },
            extra_decision_fields: json!({}),
        }
    }

    #[test]
    fn accepts_complete_teacher_impulse_input() {
        let receipt = evaluate_decision(&accepted_input(), &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Accepted);
        assert_eq!(
            receipt.expected_entry.as_ref().unwrap().market_cap_usd,
            3_120.0
        );
        assert_eq!(
            receipt.source_slot_role,
            "grouping_only_not_ordering_or_inventory_proof"
        );
        assert!(receipt.reason_codes.is_empty());
    }

    #[test]
    fn receipt_hash_is_deterministic() {
        let input = accepted_input();
        let a = evaluate_decision(&input, &StrategyConfig::default());
        let b = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(a.deterministic_event_id, b.deterministic_event_id);
        assert_eq!(a.receipt_hash, b.receipt_hash);
        assert!(!a.receipt_hash.is_empty());
    }

    #[test]
    fn mayhem_unavailable_is_fail_closed() {
        let mut input = accepted_input();
        input.mayhem.status = MayhemStatus::Unavailable;
        input.mayhem.reason = Some("mayhem_source_check_unavailable".to_string());
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"mayhem_fail_closed".to_string()));
        assert!(receipt.expected_entry.is_none());
    }

    #[test]
    fn rejects_late_state_without_treating_source_slot_as_order() {
        let mut input = accepted_input();
        input.provider.decode_complete_ts_ms = Some(900);
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Rejected);
        assert!(receipt
            .reason_codes
            .contains(&"decode_before_receive".to_string()));
        assert_eq!(
            receipt.source_slot_role,
            "grouping_only_not_ordering_or_inventory_proof"
        );
    }

    #[test]
    fn canonical_state_required_for_executable_entry() {
        let mut input = accepted_input();
        input.canonical_state = None;
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"canonical_state_missing".to_string()));
        assert!(receipt.expected_entry.is_none());
    }

    #[test]
    fn canonical_market_cap_overrides_candidate_market_cap() {
        let mut input = accepted_input();
        input.candidate.market_cap_usd = Some(2_000.0);
        input.canonical_state.as_mut().unwrap().market_cap_usd = Some(9_000.0);
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Rejected);
        assert!(receipt
            .reason_codes
            .contains(&"market_cap_above_max".to_string()));
        assert_eq!(receipt.calculated_market_cap_usd, Some(9_000.0));
    }

    #[test]
    fn canonical_state_after_candidate_ready_is_unavailable() {
        let mut input = accepted_input();
        input.provider.candidate_ready_ts_ms = Some(1_008);
        input.canonical_state.as_mut().unwrap().observed_at_ms = 1_009;
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"canonical_state_after_decision".to_string()));
        assert!(receipt.expected_entry.is_none());
    }

    #[test]
    fn mayhem_check_after_candidate_ready_is_unavailable() {
        let mut input = accepted_input();
        input.provider.candidate_ready_ts_ms = Some(1_008);
        input.mayhem.checked_at_ms = Some(1_009);
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"mayhem_check_after_decision".to_string()));
        assert!(receipt.expected_entry.is_none());
    }

    #[test]
    fn accepted_receipt_does_not_assume_submit_inventory_or_exit() {
        let receipt = evaluate_decision(&accepted_input(), &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Accepted);
        assert!(receipt.observed_execution.submitted_signature.is_none());
        assert!(receipt.observed_execution.submitted_at_ms.is_none());
        assert!(receipt.observed_execution.landed_at_ms.is_none());
        assert!(receipt
            .observed_execution
            .first_inventory_proof_at_ms
            .is_none());
        assert!(receipt.observed_execution.exit_signature.is_none());
        assert!(receipt.observed_execution.gross_result_lamports.is_none());
        assert!(receipt.observed_execution.net_result_lamports.is_none());
    }

    #[test]
    fn accepted_entry_requires_executable_quote_and_min_out() {
        let mut input = accepted_input();
        input.execution.expected_tokens_out_raw = None;
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"expected_tokens_out_missing".to_string()));
        assert!(receipt.expected_entry.is_none());

        let mut input = accepted_input();
        input.execution.min_tokens_out_raw = None;
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"min_tokens_out_missing".to_string()));
        assert!(receipt.expected_entry.is_none());
    }

    #[test]
    fn min_tokens_out_cannot_exceed_expected_tokens_out() {
        let mut input = accepted_input();
        input.execution.expected_tokens_out_raw = Some(10_000);
        input.execution.min_tokens_out_raw = Some(10_001);
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Rejected);
        assert!(receipt
            .reason_codes
            .contains(&"min_tokens_out_above_expected".to_string()));
        assert!(receipt.expected_entry.is_none());
    }

    #[test]
    fn fee_tip_slippage_and_raw_token_units_are_preserved() {
        let mut input = accepted_input();
        input.execution.position_size_lamports = 2_000_000;
        input.execution.priority_fee_lamports = 123;
        input.execution.jito_tip_lamports = 456;
        input.execution.other_tip_lamports = 789;
        input.execution.slippage_bps = 321;
        input.execution.expected_tokens_out_raw = Some(12_345_678_901_234_567_890);
        input.execution.min_tokens_out_raw = Some(11_111_111_111_111_111_111);
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Accepted);
        assert_eq!(receipt.fee_model.priority_fee_lamports, 123);
        assert_eq!(receipt.fee_model.jito_tip_lamports, 456);
        assert_eq!(receipt.fee_model.other_tip_lamports, 789);
        let entry = receipt.expected_entry.unwrap();
        assert_eq!(entry.position_size_lamports, 2_000_000);
        assert_eq!(entry.slippage_bps, 321);
        assert_eq!(
            entry.expected_tokens_out_raw,
            Some(12_345_678_901_234_567_890)
        );
        assert_eq!(entry.min_tokens_out_raw, Some(11_111_111_111_111_111_111));
    }
}
