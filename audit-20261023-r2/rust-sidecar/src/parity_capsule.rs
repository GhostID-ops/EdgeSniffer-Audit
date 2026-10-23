use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::parity_core::{
    stable_hash, CandidateFeatures, CanonicalState, DecisionInput, ExecutionModel, MayhemEvidence,
    ProviderEvidence, ProviderSource, PARITY_SCHEMA_VERSION,
};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CandidateCapsule {
    #[serde(default)]
    pub schema_version: Option<String>,
    #[serde(default)]
    pub strategy: Option<String>,
    pub source: ProviderSource,
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
    pub candidate_ready_ts_ms: Option<u64>,
    #[serde(default)]
    pub transaction_signature: Option<String>,
    pub candidate: CandidateFeatures,
    #[serde(default)]
    pub canonical_state: Option<CanonicalState>,
    pub mayhem: MayhemEvidence,
    pub execution: ExecutionModel,
    #[serde(default)]
    pub extra_decision_fields: Value,
}

pub fn decision_input_from_capsule(capsule: CandidateCapsule) -> Result<DecisionInput> {
    let raw_record_hash = match capsule.raw_record_hash.as_deref().map(str::trim) {
        Some(hash) if !hash.is_empty() => hash.to_string(),
        _ => capsule
            .raw_record
            .as_ref()
            .map(stable_hash)
            .unwrap_or_default(),
    };

    let raw_record_identity = capsule
        .raw_record_identity
        .as_deref()
        .map(str::trim)
        .filter(|identity| !identity.is_empty())
        .map(str::to_string)
        .unwrap_or_else(|| inferred_identity(&capsule, &raw_record_hash));

    let extra_decision_fields = if capsule.extra_decision_fields.is_null() {
        json!({})
    } else {
        capsule.extra_decision_fields
    };

    let input = DecisionInput {
        schema_version: capsule
            .schema_version
            .unwrap_or_else(|| PARITY_SCHEMA_VERSION.to_string()),
        strategy: capsule
            .strategy
            .unwrap_or_else(|| PARITY_SCHEMA_VERSION.to_string()),
        provider: ProviderEvidence {
            source: capsule.source,
            raw_record_identity,
            raw_record_hash,
            source_slot: capsule.source_slot,
            source_transaction_index: capsule.source_transaction_index,
            provider_sequence: capsule.provider_sequence,
            local_receive_ts_ms: capsule.local_receive_ts_ms,
            decode_complete_ts_ms: capsule.decode_complete_ts_ms,
            candidate_ready_ts_ms: capsule.candidate_ready_ts_ms,
            transaction_signature: capsule.transaction_signature,
        },
        candidate: capsule.candidate,
        canonical_state: capsule.canonical_state,
        mayhem: capsule.mayhem,
        execution: capsule.execution,
        extra_decision_fields,
    };

    validate_decision_input_boundary(&input)?;
    Ok(input)
}

pub fn parse_capsule_line(line: &str, line_no: u64) -> Result<CandidateCapsule> {
    serde_json::from_str(line).with_context(|| format!("parse CandidateCapsule line {line_no}"))
}

pub fn parse_decision_input_line(line: &str, line_no: u64) -> Result<DecisionInput> {
    match serde_json::from_str::<DecisionInput>(line) {
        Ok(input) => Ok(input),
        Err(input_error) => {
            let capsule = parse_capsule_line(line, line_no)
                .with_context(|| format!("line {line_no} was not DecisionInput: {input_error}"))?;
            decision_input_from_capsule(capsule)
        }
    }
}

fn inferred_identity(capsule: &CandidateCapsule, raw_record_hash: &str) -> String {
    let source = serde_json::to_string(&capsule.source)
        .unwrap_or_else(|_| "\"unknown\"".to_string())
        .trim_matches('"')
        .to_string();
    format!(
        "source:{source}:slot:{}:tx_index:{}:sig:{}:mint:{}:raw:{}",
        capsule
            .source_slot
            .map(|value| value.to_string())
            .unwrap_or_else(|| "unknown".to_string()),
        capsule
            .source_transaction_index
            .map(|value| value.to_string())
            .unwrap_or_else(|| "unknown".to_string()),
        capsule
            .transaction_signature
            .as_deref()
            .unwrap_or("unknown"),
        capsule.candidate.mint,
        raw_record_hash
    )
}

fn validate_decision_input_boundary(input: &DecisionInput) -> Result<()> {
    if input.provider.local_receive_ts_ms == 0 {
        anyhow::bail!("provider.local_receive_ts_ms must be non-zero");
    }
    if input.candidate.mint.trim().is_empty() {
        anyhow::bail!("candidate.mint is required");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parity_core::{
        evaluate_decision, CandidateFeatures, CanonicalState, DecisionStatus, ExecutionModel,
        MayhemEvidence, MayhemStatus, ProviderSource, StrategyConfig, Venue,
    };

    fn capsule() -> CandidateCapsule {
        CandidateCapsule {
            schema_version: None,
            strategy: None,
            source: ProviderSource::ShredStream,
            raw_record_identity: None,
            raw_record_hash: None,
            raw_record: Some(json!({
                "slot": 11,
                "transactionIndex": 2,
                "signature": "sig111",
                "mint": "Mint111111111111111111111111111111111111111"
            })),
            source_slot: Some(11),
            source_transaction_index: Some(2),
            provider_sequence: Some("shred:11:2".to_string()),
            local_receive_ts_ms: 1_000,
            decode_complete_ts_ms: Some(1_003),
            candidate_ready_ts_ms: Some(1_006),
            transaction_signature: Some("sig111".to_string()),
            candidate: CandidateFeatures {
                mint: "Mint111111111111111111111111111111111111111".to_string(),
                token_program: Some("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA".to_string()),
                source_ix_data_base64: Some("AQID".to_string()),
                creation_proven: Some(true),
                creation_age_ms: Some(250),
                market_cap_usd: Some(2_100.0),
                net_buy_sol_60: Some(5.0),
                buy_count_100ms: Some(1),
                buy_count_250ms: Some(2),
                buy_count_500ms: Some(4),
                buy_count_1000ms: Some(5),
                buy_count_60: Some(5),
                sell_pressure_60: Some(0.05),
                dominant_buy_share_60: Some(0.34),
                unique_wallets_60: Some(4),
                unique_per_buy_60: Some(0.8),
                first_buyer_quote_share_bps: Some(3_400),
                largest_wallet_quote_share_bps: Some(3_400),
            },
            canonical_state: Some(CanonicalState {
                venue: Venue::PumpFun,
                source: ProviderSource::Yellowstone,
                state_identity: "curve:111".to_string(),
                state_hash: "statehash111".to_string(),
                observed_at_ms: 1_004,
                source_slot: Some(11),
                market_cap_usd: Some(2_120.0),
                curve_complete: Some(false),
                unavailable_reason: None,
            }),
            mayhem: MayhemEvidence {
                status: MayhemStatus::Clean,
                reason: None,
                checked_at_ms: Some(1_004),
            },
            execution: ExecutionModel {
                position_size_lamports: 1_000_000,
                priority_fee_lamports: 5_000,
                jito_tip_lamports: 10_000,
                other_tip_lamports: 0,
                slippage_bps: 500,
                expected_tokens_out_raw: Some(10_000),
                min_tokens_out_raw: Some(9_500),
            },
            extra_decision_fields: Value::Null,
        }
    }

    #[test]
    fn capsule_defaults_to_teacher_impulse_schema_and_hashes_raw_record() {
        let input = decision_input_from_capsule(capsule()).unwrap();
        assert_eq!(input.schema_version, PARITY_SCHEMA_VERSION);
        assert_eq!(input.strategy, PARITY_SCHEMA_VERSION);
        assert!(!input.provider.raw_record_hash.is_empty());
        assert!(input
            .provider
            .raw_record_identity
            .contains("source:shred_stream"));
        assert_eq!(input.extra_decision_fields, json!({}));
    }

    #[test]
    fn existing_decision_input_lines_parse_without_capsule_rewrite() {
        let input = decision_input_from_capsule(capsule()).unwrap();
        let line = serde_json::to_string(&input).unwrap();
        let parsed = parse_decision_input_line(&line, 1).unwrap();
        assert_eq!(parsed, input);
    }

    #[test]
    fn missing_receive_timestamp_is_invalid_for_capsules() {
        let mut capsule = capsule();
        capsule.local_receive_ts_ms = 0;
        let error = decision_input_from_capsule(capsule).unwrap_err();
        assert!(error
            .to_string()
            .contains("provider.local_receive_ts_ms must be non-zero"));
    }

    #[test]
    fn fixture_capsules_replay_to_stable_receipts() {
        const CAPSULES: &str = include_str!("../fixtures/teacher_impulse/capsules.jsonl");
        let config = StrategyConfig::default();
        let mut statuses = Vec::new();
        let mut receipt_hashes = Vec::new();

        for (index, line) in CAPSULES
            .lines()
            .filter(|line| !line.trim().is_empty())
            .enumerate()
        {
            let capsule = parse_capsule_line(line, (index + 1) as u64).unwrap();
            let input = decision_input_from_capsule(capsule).unwrap();
            let receipt_a = evaluate_decision(&input, &config);
            let receipt_b = evaluate_decision(&input, &config);

            assert_eq!(
                receipt_a.deterministic_event_id,
                receipt_b.deterministic_event_id
            );
            assert_eq!(receipt_a.receipt_hash, receipt_b.receipt_hash);
            assert_eq!(
                receipt_a.source_slot_role,
                "grouping_only_not_ordering_or_inventory_proof"
            );

            let serialized_input = serde_json::to_string(&input).unwrap();
            let reparsed =
                parse_decision_input_line(&serialized_input, (index + 1) as u64).unwrap();
            assert_eq!(reparsed, input);

            statuses.push(receipt_a.status);
            receipt_hashes.push(receipt_a.receipt_hash);
        }

        assert_eq!(
            statuses,
            vec![
                DecisionStatus::Accepted,
                DecisionStatus::Unavailable,
                DecisionStatus::Unavailable,
            ]
        );
        assert_eq!(receipt_hashes.len(), 3);
        assert!(receipt_hashes.iter().all(|hash| !hash.is_empty()));
    }
}
