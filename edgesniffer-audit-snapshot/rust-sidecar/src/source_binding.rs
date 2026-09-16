use serde_json::{json, Value};
use sha2::{Digest, Sha256};

pub const CANDIDATE_SOURCE_SCHEMA_VERSION: &str = "teacher-impulse-candidate-source-v1";

#[derive(Debug, Clone, Default)]
pub struct SourceBindingConfig {
    pub required_lane: Option<String>,
    pub allowed_lanes: Vec<String>,
    pub required_policy: Option<String>,
    pub slippage_bps: u64,
}

impl SourceBindingConfig {
    pub fn with_default_slippage() -> Self {
        Self {
            slippage_bps: 500,
            ..Self::default()
        }
    }
}

pub fn normalize_source_line(
    line: &str,
    config: &SourceBindingConfig,
) -> Result<Option<Value>, String> {
    let row: Value =
        serde_json::from_str(line).map_err(|err| format!("parse source json: {err}"))?;
    normalize_source_value(&row, line, config)
}

pub fn normalize_source_value(
    row: &Value,
    raw_line: &str,
    config: &SourceBindingConfig,
) -> Result<Option<Value>, String> {
    if !is_supported_candidate_selected(row) {
        return Ok(None);
    }
    if let Some(required_lane) = config.required_lane.as_deref() {
        if string_at_any(row, &[&["lane"], &["features", "lane"]]).as_deref() != Some(required_lane)
        {
            return Ok(None);
        }
    }
    if !config.allowed_lanes.is_empty() {
        let lane = string_at_any(row, &[&["lane"], &["features", "lane"]]);
        let allowed = lane
            .as_ref()
            .is_some_and(|lane| config.allowed_lanes.iter().any(|allowed| allowed == lane));
        if !allowed {
            return Ok(None);
        }
    }
    if let Some(required_policy) = config.required_policy.as_deref() {
        if string_at_any(row, &[&["policy"], &["features", "policy"]]).as_deref()
            != Some(required_policy)
        {
            return Ok(None);
        }
    }

    let raw_record_hash = sha256_hex(raw_line.as_bytes());
    let key = string_at_any(
        row,
        &[&["key"], &["features", "key"], &["canonicalCandidateId"]],
    )
    .unwrap_or_else(|| raw_record_hash.clone());
    let mint = string_at_any(row, &[&["mint"], &["features", "mint"]]).unwrap_or_default();
    let source_signature = string_at_any(
        row,
        &[
            &["sourceSignature"],
            &["features", "sourceSignature"],
            &["transactionSignature"],
            &["signature"],
        ],
    );
    let source_slot = u64_at_any(
        row,
        &[&["sourceSlot"], &["features", "sourceSlot"], &["slot"]],
    );
    let candidate_ready_ts_ms = u64_at_any(
        row,
        &[
            &["candidateReadyTsMs"],
            &["entryAtMs"],
            &["selectedAtMs"],
            &["atMs"],
            &["features", "observedAtMs"],
        ],
    );
    let local_receive_ts_ms = u64_at_any(
        row,
        &[
            &["localReceiveTsMs"],
            &["features", "observedAtMs"],
            &["entryAtMs"],
            &["atMs"],
        ],
    )
    .or(candidate_ready_ts_ms)
    .unwrap_or(0);
    let quote = row.get("directEntryQuote");
    let curve = quote.and_then(|quote| quote.get("curveSnapshot"));
    let expected_tokens_out = u128_at_any(
        row,
        &[
            &["directEntryQuote", "tokensOutRaw"],
            &["expectedTokensOutRaw"],
            &["features", "tokensOutRaw"],
        ],
    );
    let min_tokens_out = u128_at_any(
        row,
        &[
            &["minTokensOutRaw"],
            &["submittedMinTokensOutRaw"],
            &["directEntryQuote", "minTokensOutRaw"],
        ],
    )
    .or_else(|| expected_tokens_out.map(|raw| min_out_from_slippage(raw, config.slippage_bps)));

    let mut out = json!({
        "schemaVersion": CANDIDATE_SOURCE_SCHEMA_VERSION,
        "sourceSchemaVersion": string_at_any(row, &[&["schemaVersion"]]),
        "sourceEvent": string_at_any(row, &[&["event"]]),
        "rawRecordIdentity": key,
        "rawRecordHash": raw_record_hash,
        "source": string_at_any(row, &[&["source"], &["sourceProvider"], &["provider"]]).unwrap_or_else(|| "unknown".to_string()),
        "providerSequence": string_at_any(row, &[&["providerSequence"], &["evaluationSequence"], &["sequence"], &["seq"]]),
        "transactionSignature": source_signature,
        "sourceSignature": string_at_any(row, &[&["sourceSignature"], &["features", "sourceSignature"]]),
        "sourceSlot": source_slot,
        "sourceInstructionIndex": u64_at_any(row, &[&["sourceInstructionIndex"], &["features", "sourceInstructionIndex"], &["instructionIndex"]]),
        "localReceiveTsMs": local_receive_ts_ms,
        "decodeCompleteTsMs": u64_at_any(row, &[&["decodeCompleteTsMs"], &["decodedAtMs"]]),
        "candidateReadyTsMs": candidate_ready_ts_ms,
        "lane": string_at_any(row, &[&["lane"], &["features", "lane"]]),
        "policy": string_at_any(row, &[&["policy"], &["features", "policy"]]),
        "mint": mint,
        "tokenProgramId": string_at_any(row, &[
            &["tokenProgramId"],
            &["features", "tokenProgramId"],
            &["features", "tokenProgram"],
            &["directEntryQuote", "curveSnapshot", "tokenProgramId"],
        ]),
        "sourceIxDataBase64": string_at_any(row, &[&["sourceIxDataBase64"], &["features", "sourceIxDataBase64"]]),
        "creationProven": bool_at_any(row, &[&["creationProven"], &["features", "creationProven"], &["created"]]),
        "creationAgeMs": u64_at_any(row, &[&["creationAgeMs"], &["features", "creationAgeMs"], &["ageMs"], &["features", "ageMs"]]),
        "entryMarketCapUsd": f64_at_any(row, &[&["entryMarketCapUsd"], &["features", "entryMarketCapUsd"], &["features", "marketCapUsd"]]),
        "netBuySol60": f64_at_any(row, &[&["netBuySol60"], &["features", "netBuySol60"]]),
        "buyCount100ms": u32_at_any(row, &[&["buyCount100ms"], &["features", "buyCount100ms"]]),
        "buyCount250ms": u32_at_any(row, &[&["buyCount250ms"], &["features", "buyCount250ms"]]),
        "buyCount500ms": u32_at_any(row, &[&["buyCount500ms"], &["features", "buyCount500ms"]]),
        "buyCount1000ms": u32_at_any(row, &[&["buyCount1000ms"], &["features", "buyCount1000ms"]]),
        "buyCount60": u32_at_any(row, &[&["buyCount60"], &["features", "buyCount60"]]),
        "sellPressure60": f64_at_any(row, &[&["sellPressure60"], &["features", "sellPressure60"], &["sellPressure"], &["features", "sellPressure"]]),
        "dominantBuyShare60": f64_at_any(row, &[&["dominantBuyShare60"], &["features", "dominantBuyShare60"], &["dominance"], &["features", "dominance"]]),
        "uniqueWallets60": u32_at_any(row, &[&["uniqueWallets60"], &["features", "uniqueWallets60"]]),
        "uniquePerBuy60": f64_at_any(row, &[&["uniquePerBuy60"], &["features", "uniquePerBuy60"]]),
        "firstBuyerQuoteShareBps": u32_at_any(row, &[&["firstBuyerQuoteShareBps"], &["features", "firstBuyerQuoteShareBps"]]),
        "largestWalletQuoteShareBps": u32_at_any(row, &[&["largestWalletQuoteShareBps"], &["features", "largestWalletQuoteShareBps"]]),
        "canonicalStateIdentity": string_at_any(row, &[&["canonicalStateIdentity"], &["bondingCurve"], &["directEntryQuote", "curveSnapshot", "pda"], &["directEntryQuote", "curveSnapshot", "bondingCurve"]]),
        "canonicalStateHash": string_at_any(row, &[&["canonicalStateHash"], &["stateHash"]]),
        "canonicalStateSource": string_at_any(row, &[&["canonicalStateSource"], &["reserveSource"], &["directEntryQuote", "curveSnapshot", "source"]]).unwrap_or_else(|| "unknown".to_string()),
        "canonicalStateObservedAtMs": u64_at_any(row, &[&["canonicalStateObservedAtMs"], &["reserveObservedAtMs"], &["directEntryQuote", "curveSnapshot", "observedAtMs"]]),
        "canonicalStateSlot": u64_at_any(row, &[&["canonicalStateSlot"], &["reserveSlot"], &["directEntryQuote", "curveSnapshot", "slot"]]),
        "directMarketCapUsd": f64_at_any(row, &[&["directMarketCapUsd"], &["directEntryQuote", "marketCapUsd"], &["entryMarketCapUsd"]]),
        "curveComplete": bool_at_any(row, &[&["curveComplete"], &["directEntryQuote", "curveSnapshot", "complete"]]),
        "mayhemHardBlock": bool_at_any(row, &[&["mayhemHardBlock"], &["isMayhem"], &["features", "isMayhem"]]).unwrap_or(false),
        "mayhemUnavailable": bool_at_any(row, &[&["mayhemUnavailable"], &["isWeakMayhemRecovered"], &["features", "isWeakMayhemRecovered"]]),
        "mayhemStatus": string_at_any(row, &[&["mayhemStatus"], &["mayhem", "status"]]),
        "mayhemBlockReason": string_at_any(row, &[&["mayhemBlockReason"], &["features", "mayhemBlockReason"], &["mayhem", "reason"]]),
        "shadowSolInLamports": u64_at_any(row, &[&["shadowSolInLamports"], &["directEntryQuote", "solInLamports"]]),
        "expectedTokensOutRaw": expected_tokens_out.map(|value| value.to_string()),
        "minTokensOutRaw": min_tokens_out.map(|value| value.to_string()),
        "slippageBps": config.slippage_bps,
        "sourceBinding": {
            "name": "teacher_impulse_source_emitter",
            "mode": "no_submit_source_normalizer",
            "rawRecordHashSource": "source_line_sha256",
            "doesNotInferMayhemClean": true,
            "doesNotSubmit": true
        },
        "rawSourceRow": row,
    });

    if quote
        .and_then(|quote| quote.get("status"))
        .and_then(Value::as_str)
        != Some("ok")
    {
        out["canonicalStateUnavailableReason"] = json!("direct_entry_quote_not_ok");
    }
    if curve.is_none() {
        out["canonicalStateUnavailableReason"] = json!("direct_entry_curve_snapshot_missing");
    }

    Ok(Some(out))
}

fn is_supported_candidate_selected(row: &Value) -> bool {
    let schema = string_at_any(row, &[&["schemaVersion"]]);
    let event = string_at_any(row, &[&["event"]]);
    schema.as_deref() == Some("two-cq-continuous-shadow-event-1")
        && event.as_deref() == Some("candidate_selected")
}

fn min_out_from_slippage(tokens_out: u128, slippage_bps: u64) -> u128 {
    let kept_bps = 10_000_u128.saturating_sub(slippage_bps.min(10_000) as u128);
    tokens_out.saturating_mul(kept_bps) / 10_000
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
    use crate::{
        decision_input_producer::{decision_input_from_candidate_value, ProducerConfig},
        parity_core::{evaluate_decision, DecisionStatus, StrategyConfig},
    };

    fn selected_line() -> &'static str {
        r#"{"schemaVersion":"two-cq-continuous-shadow-event-1","event":"candidate_selected","atMs":1200,"entryAtMs":1180,"lane":"teacher-impulse-parity-v1","policy":"no-submit","key":"k1","mint":"MintSelected","entryMarketCapUsd":2400.0,"sourceSignature":"sig-source","sourceSlot":88,"features":{"mint":"MintSelected","sourceSignature":"sig-source","sourceIxDataBase64":"AQID","tokenProgramId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA","creationProven":true,"creationAgeMs":350,"netBuySol60":4.5,"buyCount60":5,"sellPressure60":0.05,"dominantBuyShare60":0.25,"uniqueWallets60":4,"firstBuyerQuoteShareBps":2500},"directEntryQuote":{"status":"ok","solInLamports":1000000,"tokensOutRaw":"1000000000","marketCapUsd":2450.0,"curveSnapshot":{"pda":"CurveSelected","creator":"CreatorSelected","tokenProgramId":"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA","virtualSolReserves":31000000000,"virtualTokenReserves":1000000000000000,"slot":89,"observedAtMs":1175,"complete":false}},"mayhemStatus":"clean"}"#
    }

    #[test]
    fn selected_shadow_event_normalizes_to_receipt_accepted_source() {
        let cfg = SourceBindingConfig {
            required_lane: Some("teacher-impulse-parity-v1".to_string()),
            required_policy: Some("no-submit".to_string()),
            slippage_bps: 500,
            ..SourceBindingConfig::default()
        };
        let normalized = normalize_source_line(selected_line(), &cfg)
            .unwrap()
            .expect("selected row");
        let input = decision_input_from_candidate_value(
            &normalized,
            &normalized.to_string(),
            &ProducerConfig::default(),
        )
        .unwrap();
        let receipt = evaluate_decision(&input, &StrategyConfig::default());

        assert_eq!(normalized["schemaVersion"], CANDIDATE_SOURCE_SCHEMA_VERSION);
        assert_eq!(normalized["rawRecordIdentity"], "k1");
        assert_eq!(normalized["minTokensOutRaw"], "950000000");
        assert_eq!(receipt.status, DecisionStatus::Accepted);
        assert_eq!(receipt.calculated_market_cap_usd, Some(2450.0));
    }

    #[test]
    fn lane_mismatch_is_ignored_without_emitting_source_row() {
        let cfg = SourceBindingConfig {
            required_lane: Some("other-lane".to_string()),
            slippage_bps: 500,
            ..SourceBindingConfig::default()
        };
        assert!(normalize_source_line(selected_line(), &cfg)
            .unwrap()
            .is_none());
    }

    #[test]
    fn missing_mayhem_is_not_inferred_clean() {
        let line = selected_line().replace(r#","mayhemStatus":"clean""#, "");
        let normalized =
            normalize_source_line(&line, &SourceBindingConfig::with_default_slippage())
                .unwrap()
                .expect("selected row");
        let input = decision_input_from_candidate_value(
            &normalized,
            &normalized.to_string(),
            &ProducerConfig::default(),
        )
        .unwrap();
        let receipt = evaluate_decision(&input, &StrategyConfig::default());

        assert_eq!(receipt.status, DecisionStatus::Unavailable);
        assert!(receipt
            .reason_codes
            .contains(&"mayhem_fail_closed".to_string()));
    }
}
