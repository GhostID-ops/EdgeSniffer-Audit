use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};

use crate::parity_core::{
    CandidateFeatures, CanonicalState, DecisionInput, ExecutionModel, MayhemEvidence, MayhemStatus,
    ProviderEvidence, ProviderSource, Venue, PARITY_SCHEMA_VERSION,
};

const DEFAULT_POSITION_SIZE_LAMPORTS: u64 = 1_000_000;
const DEFAULT_SLIPPAGE_BPS: u64 = 500;

#[derive(Debug, Clone)]
pub struct ProducerConfig {
    pub position_size_lamports: u64,
    pub priority_fee_lamports: u64,
    pub jito_tip_lamports: u64,
    pub other_tip_lamports: u64,
    pub slippage_bps: u64,
}

impl Default for ProducerConfig {
    fn default() -> Self {
        Self {
            position_size_lamports: DEFAULT_POSITION_SIZE_LAMPORTS,
            priority_fee_lamports: 0,
            jito_tip_lamports: 0,
            other_tip_lamports: 0,
            slippage_bps: DEFAULT_SLIPPAGE_BPS,
        }
    }
}

pub fn decision_input_from_candidate_line(
    line: &str,
    config: &ProducerConfig,
) -> Result<DecisionInput, String> {
    let row: Value =
        serde_json::from_str(line).map_err(|err| format!("parse candidate json: {err}"))?;
    decision_input_from_candidate_value(&row, line, config)
}

pub fn decision_input_from_candidate_value(
    row: &Value,
    raw_line: &str,
    config: &ProducerConfig,
) -> Result<DecisionInput, String> {
    let raw_record_hash = string_at_any(row, &[&["rawRecordHash"], &["raw_record_hash"]])
        .unwrap_or_else(|| sha256_hex(raw_line.as_bytes()));
    let transaction_signature = string_at_any(
        row,
        &[
            &["transactionSignature"],
            &["transaction_signature"],
            &["sourceSignature"],
            &["source_signature"],
            &["signature"],
            &["signal", "signature"],
        ],
    );
    let source_slot = u64_at_any(
        row,
        &[
            &["sourceSlot"],
            &["source_slot"],
            &["slot"],
            &["signal", "slot"],
        ],
    );
    let tx_index = u64_at_any(
        row,
        &[
            &["sourceTransactionIndex"],
            &["source_transaction_index"],
            &["transactionIndex"],
            &["transaction_index"],
            &["sourceInstructionIndex"],
            &["instructionIndex"],
        ],
    )
    .and_then(|value| u32::try_from(value).ok());
    let raw_record_identity = string_at_any(
        row,
        &[
            &["rawRecordIdentity"],
            &["raw_record_identity"],
            &["canonicalCandidateId"],
            &["canonical_candidate_id"],
            &["key"],
            &["id"],
        ],
    )
    .unwrap_or_else(|| {
        let sig = transaction_signature.as_deref().unwrap_or("missing_sig");
        let slot = source_slot
            .map(|value| value.to_string())
            .unwrap_or_else(|| "missing_slot".to_string());
        let index = tx_index
            .map(|value| value.to_string())
            .unwrap_or_else(|| "missing_index".to_string());
        format!("candidate:slot:{slot}:index:{index}:sig:{sig}:hash:{raw_record_hash}")
    });

    let local_receive_ts_ms = u64_at_any(
        row,
        &[
            &["localReceiveTsMs"],
            &["local_receive_ts_ms"],
            &["observedAtMs"],
            &["observed_at_ms"],
            &["sourceEventAtMs"],
            &["eventTsMs"],
            &["signal", "triggerReceivedAtMs"],
            &["detectedAtMs"],
            &["detected_at_ms"],
            &["tsMs"],
            &["ts_ms"],
        ],
    )
    .unwrap_or(0);
    let decode_complete_ts_ms = u64_at_any(
        row,
        &[
            &["decodeCompleteTsMs"],
            &["decode_complete_ts_ms"],
            &["decodedAtMs"],
            &["decoded_at_ms"],
        ],
    );
    let candidate_ready_ts_ms = u64_at_any(
        row,
        &[
            &["candidateReadyTsMs"],
            &["candidate_ready_ts_ms"],
            &["selectedAtMs"],
            &["selected_at_ms"],
            &["evalAtMs"],
            &["observedAtMs"],
            &["observed_at_ms"],
        ],
    );

    let mint = string_at_any(
        row,
        &[&["mint"], &["candidate", "mint"], &["signal", "mint"]],
    )
    .unwrap_or_default();
    let token_program = string_at_any(
        row,
        &[
            &["tokenProgram"],
            &["token_program"],
            &["tokenProgramId"],
            &["token_program_id"],
            &["candidate", "token_program"],
            &["signal", "tokenProgramId"],
        ],
    );

    let source_ix_data_base64 = string_at_any(
        row,
        &[
            &["sourceIxDataBase64"],
            &["source_ix_data_base64"],
            &["sourceInstructionDataBase64"],
            &["source_instruction_data_base64"],
            &["candidate", "source_ix_data_base64"],
            &["signal", "sourceIxDataBase64"],
        ],
    );

    let canonical_state = canonical_state_from_row(row, source_slot, candidate_ready_ts_ms);

    let mayhem_status = if bool_at_any(
        row,
        &[
            &["mayhemHardBlock"],
            &["mayhem_hard_block"],
            &["isMayhem"],
            &["features", "isMayhem"],
        ],
    )
    .unwrap_or(false)
    {
        MayhemStatus::HardBlock
    } else if bool_at_any(
        row,
        &[
            &["mayhemUnavailable"],
            &["mayhem_unavailable"],
            &["isWeakMayhemRecovered"],
            &["features", "isWeakMayhemRecovered"],
        ],
    )
    .unwrap_or(false)
        || string_at_any(
            row,
            &[
                &["mayhemBlockReason"],
                &["features", "mayhemBlockReason"],
                &["mayhem", "reason"],
            ],
        )
        .is_some_and(|reason| reason == "mayhem_source_check_unavailable")
    {
        MayhemStatus::Unavailable
    } else if bool_at_any(row, &[&["mayhemClean"], &["mayhem_clean"], &["isClean"]])
        .unwrap_or(false)
        || string_at_any(row, &[&["mayhemStatus"], &["mayhem", "status"]])
            .is_some_and(|status| status == "clean")
    {
        MayhemStatus::Clean
    } else {
        MayhemStatus::Unavailable
    };

    let mut extra = Map::new();
    extra.insert("sourceRow".to_string(), row.clone());
    extra.insert(
        "producer".to_string(),
        json!({
            "name": "teacher_impulse_candidate_producer",
            "rawRecordHashSource": "provided_or_source_line_sha256",
            "missingMayhemDefaultsToUnavailable": true,
        }),
    );

    Ok(DecisionInput {
        schema_version: PARITY_SCHEMA_VERSION.to_string(),
        strategy: PARITY_SCHEMA_VERSION.to_string(),
        provider: ProviderEvidence {
            source: provider_source(row),
            raw_record_identity,
            raw_record_hash,
            source_slot,
            source_transaction_index: tx_index,
            provider_sequence: string_at_any(
                row,
                &[
                    &["providerSequence"],
                    &["provider_sequence"],
                    &["sequence"],
                    &["seq"],
                    &["evaluationSequence"],
                ],
            ),
            local_receive_ts_ms,
            decode_complete_ts_ms,
            candidate_ready_ts_ms,
            transaction_signature,
        },
        candidate: CandidateFeatures {
            mint,
            token_program,
            source_ix_data_base64,
            creation_proven: bool_at_any(
                row,
                &[
                    &["creationProven"],
                    &["creation_proven"],
                    &["created"],
                    &["features", "creationProven"],
                ],
            ),
            creation_age_ms: u64_at_any(
                row,
                &[
                    &["creationAgeMs"],
                    &["creation_age_ms"],
                    &["ageMs"],
                    &["eventLagMs"],
                    &["features", "ageMs"],
                ],
            ),
            market_cap_usd: f64_at_any(
                row,
                &[
                    &["marketCapUsd"],
                    &["market_cap_usd"],
                    &["entryMarketCapUsd"],
                    &["entry_market_cap_usd"],
                    &["signal", "inferredMarketCapUsd"],
                    &["context", "latestMarketCapUsd"],
                    &["contextSnapshot", "entryMarketCapUsd"],
                    &["directMarketCapUsd"],
                    &["features", "marketCapUsd"],
                ],
            ),
            net_buy_sol_60: f64_at_any(
                row,
                &[
                    &["netBuySol60"],
                    &["net_buy_sol_60"],
                    &["context", "netBuySol60"],
                    &["contextSnapshot", "netBuySol60"],
                    &["features", "netBuySol60"],
                ],
            ),
            buy_count_100ms: u32_at_any(row, &[&["buyCount100ms"], &["features", "buyCount100ms"]]),
            buy_count_250ms: u32_at_any(row, &[&["buyCount250ms"], &["features", "buyCount250ms"]]),
            buy_count_500ms: u32_at_any(row, &[&["buyCount500ms"], &["features", "buyCount500ms"]]),
            buy_count_1000ms: u32_at_any(
                row,
                &[&["buyCount1000ms"], &["features", "buyCount1000ms"]],
            ),
            buy_count_60: u32_at_any(
                row,
                &[
                    &["buyCount60"],
                    &["context", "buyCount60"],
                    &["contextSnapshot", "buyCount60"],
                    &["features", "buyCount60"],
                ],
            ),
            sell_pressure_60: f64_at_any(
                row,
                &[
                    &["sellPressure60"],
                    &["sell_pressure_60"],
                    &["sellPressure"],
                    &["context", "sellPressure60"],
                    &["contextSnapshot", "sellPressure60"],
                    &["features", "sellPressure60"],
                    &["features", "sellPressure"],
                ],
            ),
            dominant_buy_share_60: f64_at_any(
                row,
                &[
                    &["dominantBuyShare60"],
                    &["dominant_buy_share_60"],
                    &["dominance"],
                    &["context", "dominantBuyShare60"],
                    &["contextSnapshot", "dominantBuyShare60"],
                    &["features", "dominantBuyShare60"],
                    &["features", "dominance"],
                ],
            ),
            unique_wallets_60: u32_at_any(
                row,
                &[
                    &["uniqueWallets60"],
                    &["unique_wallets_60"],
                    &["context", "uniqueWallets60"],
                    &["contextSnapshot", "uniqueWallets60"],
                    &["features", "uniqueWallets60"],
                ],
            ),
            unique_per_buy_60: f64_at_any(
                row,
                &[
                    &["uniquePerBuy60"],
                    &["unique_per_buy_60"],
                    &["features", "uniquePerBuy60"],
                ],
            ),
            first_buyer_quote_share_bps: u32_at_any(
                row,
                &[
                    &["firstBuyerQuoteShareBps"],
                    &["first_buyer_quote_share_bps"],
                    &["features", "firstBuyerQuoteShareBps"],
                ],
            ),
            largest_wallet_quote_share_bps: u32_at_any(
                row,
                &[
                    &["largestWalletQuoteShareBps"],
                    &["largest_wallet_quote_share_bps"],
                    &["features", "largestWalletQuoteShareBps"],
                ],
            ),
        },
        canonical_state,
        mayhem: MayhemEvidence {
            status: mayhem_status,
            reason: string_at_any(
                row,
                &[
                    &["mayhemReason"],
                    &["mayhem_reason"],
                    &["mayhemBlockReason"],
                    &["features", "mayhemBlockReason"],
                    &["mayhem", "reason"],
                ],
            ),
            checked_at_ms: u64_at_any(
                row,
                &[
                    &["mayhemCheckedAtMs"],
                    &["mayhem_checked_at_ms"],
                    &["mayhem", "checked_at_ms"],
                ],
            ),
        },
        execution: ExecutionModel {
            position_size_lamports: u64_at_any(
                row,
                &[
                    &["positionSizeLamports"],
                    &["position_size_lamports"],
                    &["shadowSolInLamports"],
                ],
            )
            .unwrap_or(config.position_size_lamports),
            priority_fee_lamports: u64_at_any(
                row,
                &[&["priorityFeeLamports"], &["priority_fee_lamports"]],
            )
            .unwrap_or(config.priority_fee_lamports),
            jito_tip_lamports: u64_at_any(row, &[&["jitoTipLamports"], &["jito_tip_lamports"]])
                .unwrap_or(config.jito_tip_lamports),
            other_tip_lamports: u64_at_any(row, &[&["otherTipLamports"], &["other_tip_lamports"]])
                .unwrap_or(config.other_tip_lamports),
            slippage_bps: u64_at_any(row, &[&["slippageBps"], &["slippage_bps"]])
                .unwrap_or(config.slippage_bps),
            expected_tokens_out_raw: u128_at_any(
                row,
                &[
                    &["expectedTokensOutRaw"],
                    &["expected_tokens_out_raw"],
                    &["shadowTokensOutRaw"],
                    &["tokensOutRaw"],
                    &["signal", "tokensOutRaw"],
                ],
            ),
            min_tokens_out_raw: u128_at_any(
                row,
                &[
                    &["minTokensOutRaw"],
                    &["min_tokens_out_raw"],
                    &["submittedMinTokensOutRaw"],
                ],
            ),
        },
        extra_decision_fields: Value::Object(extra),
    })
}

fn canonical_state_from_row(
    row: &Value,
    source_slot: Option<u64>,
    candidate_ready_ts_ms: Option<u64>,
) -> Option<CanonicalState> {
    let state_identity = string_at_any(
        row,
        &[
            &["canonicalStateIdentity"],
            &["canonical_state_identity"],
            &["stateIdentity"],
            &["bondingCurve"],
            &["bonding_curve"],
            &["pool"],
        ],
    )?;
    let observed_at_ms = u64_at_any(
        row,
        &[
            &["canonicalStateObservedAtMs"],
            &["canonical_state_observed_at_ms"],
            &["reserveObservedAtMs"],
            &["reserve_observed_at_ms"],
            &["candidateReadyTsMs"],
            &["candidate_ready_ts_ms"],
        ],
    )
    .or(candidate_ready_ts_ms)?;
    Some(CanonicalState {
        venue: venue(row),
        source: canonical_source(row),
        state_identity: state_identity.clone(),
        state_hash: string_at_any(
            row,
            &[
                &["canonicalStateHash"],
                &["canonical_state_hash"],
                &["stateHash"],
            ],
        )
        .unwrap_or_else(|| sha256_hex(state_identity.as_bytes())),
        observed_at_ms,
        source_slot: u64_at_any(
            row,
            &[
                &["canonicalStateSlot"],
                &["canonical_state_slot"],
                &["reserveSlot"],
                &["reserve_slot"],
            ],
        )
        .or(source_slot),
        market_cap_usd: f64_at_any(
            row,
            &[
                &["canonicalMarketCapUsd"],
                &["canonical_market_cap_usd"],
                &["directMarketCapUsd"],
                &["entryMarketCapUsd"],
                &["marketCapUsd"],
            ],
        ),
        curve_complete: bool_at_any(
            row,
            &[
                &["curveComplete"],
                &["curve_complete"],
                &["complete"],
                &["features", "curveComplete"],
            ],
        ),
        unavailable_reason: string_at_any(
            row,
            &[
                &["canonicalStateUnavailableReason"],
                &["canonical_state_unavailable_reason"],
            ],
        ),
    })
}

fn provider_source(row: &Value) -> ProviderSource {
    parse_source(
        string_at_any(
            row,
            &[
                &["provider"],
                &["source"],
                &["providerSource"],
                &["sourceProvider"],
                &["source_provider"],
                &["signal", "triggerProvider"],
            ],
        )
        .unwrap_or_else(|| "unknown".to_string())
        .as_str(),
    )
}

fn canonical_source(row: &Value) -> ProviderSource {
    parse_source(
        string_at_any(
            row,
            &[
                &["canonicalStateSource"],
                &["canonical_state_source"],
                &["reserveSource"],
            ],
        )
        .unwrap_or_else(|| "unknown".to_string())
        .as_str(),
    )
}

fn parse_source(raw: &str) -> ProviderSource {
    match raw.to_ascii_lowercase().as_str() {
        "shredstream" | "shred_stream" | "jito-shredstream" => ProviderSource::ShredStream,
        "yellowstone" | "grpc" | "shyft-yellowstone" => ProviderSource::Yellowstone,
        "rabbitstream" | "rabbit_stream" | "shyft-rabbitstream" => ProviderSource::RabbitStream,
        "replay" => ProviderSource::Replay,
        _ => ProviderSource::Unknown,
    }
}

fn venue(row: &Value) -> Venue {
    match string_at_any(row, &[&["venue"], &["market"], &["program"]])
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "pump_fun" | "pumpfun" | "pump.fun" => Venue::PumpFun,
        "pump_swap" | "pumpswap" | "pump.swap" => Venue::PumpSwap,
        _ => {
            if string_at_any(row, &[&["bondingCurve"], &["bonding_curve"]]).is_some() {
                Venue::PumpFun
            } else {
                Venue::Unknown
            }
        }
    }
}

fn string_at_any(row: &Value, paths: &[&[&str]]) -> Option<String> {
    paths.iter().find_map(|path| {
        value_at(row, path).and_then(|value| match value {
            Value::String(raw) if !raw.trim().is_empty() => Some(raw.trim().to_string()),
            Value::Number(number) => Some(number.to_string()),
            _ => None,
        })
    })
}

fn bool_at_any(row: &Value, paths: &[&[&str]]) -> Option<bool> {
    paths.iter().find_map(|path| {
        value_at(row, path).and_then(|value| match value {
            Value::Bool(value) => Some(*value),
            Value::String(raw) => match raw.to_ascii_lowercase().as_str() {
                "true" | "1" | "yes" => Some(true),
                "false" | "0" | "no" => Some(false),
                _ => None,
            },
            _ => None,
        })
    })
}

fn u64_at_any(row: &Value, paths: &[&[&str]]) -> Option<u64> {
    paths.iter().find_map(|path| {
        value_at(row, path).and_then(|value| match value {
            Value::Number(number) => number.as_u64(),
            Value::String(raw) => raw.parse::<u64>().ok(),
            _ => None,
        })
    })
}

fn u32_at_any(row: &Value, paths: &[&[&str]]) -> Option<u32> {
    u64_at_any(row, paths).and_then(|value| u32::try_from(value).ok())
}

fn u128_at_any(row: &Value, paths: &[&[&str]]) -> Option<u128> {
    paths.iter().find_map(|path| {
        value_at(row, path).and_then(|value| match value {
            Value::Number(number) => number.as_u64().map(u128::from),
            Value::String(raw) => raw.parse::<u128>().ok(),
            _ => None,
        })
    })
}

fn f64_at_any(row: &Value, paths: &[&[&str]]) -> Option<f64> {
    paths.iter().find_map(|path| {
        value_at(row, path).and_then(|value| match value {
            Value::Number(number) => number.as_f64(),
            Value::String(raw) => raw.parse::<f64>().ok(),
            _ => None,
        })
    })
}

fn value_at<'a>(row: &'a Value, path: &[&str]) -> Option<&'a Value> {
    let mut cursor = row;
    for key in path {
        cursor = cursor.get(*key)?;
    }
    Some(cursor)
}

fn sha256_hex(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    format!("{:x}", hasher.finalize())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::parity_core::{evaluate_decision, DecisionStatus, StrategyConfig};

    #[test]
    fn complete_candidate_maps_to_accepted_decision_input() {
        let line = r#"{"source":"shredstream","slot":7,"transactionIndex":3,"signature":"sig-a","observedAtMs":100,"decodedAtMs":105,"selectedAtMs":110,"mint":"MintA","tokenProgramId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA","sourceIxDataBase64":"AQID","creationProven":true,"ageMs":400,"entryMarketCapUsd":2400,"netBuySol60":3.5,"buyCount1000ms":4,"buyCount60":4,"sellPressure":0.05,"dominance":0.3,"uniqueWallets60":3,"firstBuyerQuoteShareBps":2500,"bondingCurve":"CurveA","reserveObservedAtMs":108,"reserveSlot":7,"directMarketCapUsd":2450,"curveComplete":false,"mayhemStatus":"clean","shadowTokensOutRaw":"1000000","minTokensOutRaw":"950000"}"#;
        let input = decision_input_from_candidate_line(line, &ProducerConfig::default()).unwrap();
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Accepted);
        assert_eq!(receipt.mint, "MintA");
        assert_eq!(receipt.calculated_market_cap_usd, Some(2450.0));
    }

    #[test]
    fn missing_mayhem_defaults_to_unavailable_fail_closed() {
        let line = r#"{"source":"shredstream","slot":7,"signature":"sig-a","observedAtMs":100,"selectedAtMs":110,"mint":"MintA","sourceIxDataBase64":"AQID","creationProven":true,"ageMs":400,"entryMarketCapUsd":2400,"netBuySol60":3.5,"sellPressure":0.05,"dominance":0.3,"uniqueWallets60":3,"bondingCurve":"CurveA","reserveObservedAtMs":108,"directMarketCapUsd":2450,"curveComplete":false,"shadowTokensOutRaw":"1000000","minTokensOutRaw":"950000"}"#;
        let input = decision_input_from_candidate_line(line, &ProducerConfig::default()).unwrap();
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"mayhem_fail_closed".to_string()));
    }

    #[test]
    fn source_row_is_hashed_when_no_raw_hash_exists() {
        let line = r#"{"source":"replay","signature":"sig-a","observedAtMs":100,"mint":"MintA"}"#;
        let input = decision_input_from_candidate_line(line, &ProducerConfig::default()).unwrap();
        assert_eq!(input.provider.raw_record_hash.len(), 64);
        assert!(input.provider.raw_record_identity.contains("hash:"));
    }

    #[test]
    fn later_canonical_state_is_preserved_for_evaluator_rejection() {
        let line = r#"{"source":"shredstream","slot":7,"signature":"sig-a","observedAtMs":100,"selectedAtMs":110,"mint":"MintA","sourceIxDataBase64":"AQID","creationProven":true,"ageMs":400,"entryMarketCapUsd":2400,"netBuySol60":3.5,"sellPressure":0.05,"dominance":0.3,"uniqueWallets60":3,"bondingCurve":"CurveA","reserveObservedAtMs":115,"directMarketCapUsd":2450,"curveComplete":false,"mayhemStatus":"clean","shadowTokensOutRaw":"1000000","minTokensOutRaw":"950000"}"#;
        let input = decision_input_from_candidate_line(line, &ProducerConfig::default()).unwrap();
        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert!(receipt
            .reason_codes
            .contains(&"canonical_state_after_decision_boundary".to_string()));
    }

    #[test]
    fn frozen_signal_shape_maps_nested_candidate_fields() {
        let line = r#"{"event":"trigger","sessionId":"s1","profile":"2cq-flow-projection-v4","signal":{"signature":"sig-frozen","wallet":"2CQ","mint":"MintFrozen","side":"buy","sourceBuySol":0.42,"tokensOutRaw":"123456789","inferredMarketCapUsd":2510.5,"triggerProvider":"shredstream","triggerReceivedAtMs":2000,"hydrationDelayMs":33,"slot":99},"context":{"buyCount60":5,"netBuySol60":4.2,"sellPressure60":0.04,"uniqueWallets60":4,"dominantBuyShare60":0.31,"latestMarketCapUsd":2520.0}}"#;
        let input = decision_input_from_candidate_line(line, &ProducerConfig::default()).unwrap();

        assert_eq!(input.provider.source, ProviderSource::ShredStream);
        assert_eq!(
            input.provider.transaction_signature.as_deref(),
            Some("sig-frozen")
        );
        assert_eq!(input.provider.source_slot, Some(99));
        assert_eq!(input.provider.local_receive_ts_ms, 2000);
        assert_eq!(input.candidate.mint, "MintFrozen");
        assert_eq!(input.candidate.market_cap_usd, Some(2510.5));
        assert_eq!(input.candidate.net_buy_sol_60, Some(4.2));
        assert_eq!(input.candidate.buy_count_60, Some(5));
        assert_eq!(input.candidate.sell_pressure_60, Some(0.04));
        assert_eq!(input.candidate.unique_wallets_60, Some(4));
        assert_eq!(input.candidate.dominant_buy_share_60, Some(0.31));
        assert_eq!(input.execution.expected_tokens_out_raw, Some(123456789));

        let receipt = evaluate_decision(&input, &StrategyConfig::default());
        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"canonical_state_missing".to_string()));
        assert!(receipt
            .reason_codes
            .contains(&"mayhem_fail_closed".to_string()));
    }

    #[test]
    fn pocket_parity_shape_maps_context_snapshot_fields() {
        let line = r#"{"event":"pocket_parity","at":"2026-08-26T00:00:00.000Z","schema":"v1","schemaHash":"abc","receiveOnly":true,"canonicalCandidateId":"cid-1","evaluationSequence":"seq-7","mint":"MintPocket","tokenProgramId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA","sourceSignature":"sig-pocket","sourceSlot":77,"sourceEventAtMs":3000,"evalAtMs":3080,"sourceInstructionIndex":2,"contextSnapshot":{"entryMarketCapUsd":2345.6,"buyCount60":7,"netBuySol60":5.8,"sellPressure60":0.08,"uniqueWallets60":5,"dominantBuyShare60":0.28},"sourceIxDataBase64":"AQIDBA==","creationProven":true,"creationAgeMs":450,"firstBuyerQuoteShareBps":3200,"bondingCurve":"CurvePocket","reserveObservedAtMs":3070,"directMarketCapUsd":2360,"curveComplete":false,"mayhemStatus":"clean","expectedTokensOutRaw":"2222","minTokensOutRaw":"2000"}"#;
        let input = decision_input_from_candidate_line(line, &ProducerConfig::default()).unwrap();
        let receipt = evaluate_decision(&input, &StrategyConfig::default());

        assert_eq!(input.provider.raw_record_identity, "cid-1");
        assert_eq!(input.provider.provider_sequence.as_deref(), Some("seq-7"));
        assert_eq!(input.provider.source_transaction_index, Some(2));
        assert_eq!(input.provider.local_receive_ts_ms, 3000);
        assert_eq!(input.provider.candidate_ready_ts_ms, Some(3080));
        assert_eq!(input.candidate.mint, "MintPocket");
        assert_eq!(input.candidate.market_cap_usd, Some(2345.6));
        assert_eq!(input.candidate.net_buy_sol_60, Some(5.8));
        assert_eq!(input.candidate.buy_count_60, Some(7));
        assert_eq!(input.candidate.sell_pressure_60, Some(0.08));
        assert_eq!(input.candidate.unique_wallets_60, Some(5));
        assert_eq!(input.candidate.dominant_buy_share_60, Some(0.28));
        assert_eq!(receipt.status, DecisionStatus::Accepted);
        assert_eq!(receipt.calculated_market_cap_usd, Some(2360.0));
    }
}
