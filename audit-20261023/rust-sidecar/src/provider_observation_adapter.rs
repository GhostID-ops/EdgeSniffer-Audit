use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::{
    parity_core::{MayhemEvidence, MayhemStatus, ProviderSource},
    teacher_impulse_emitter::{ObservationSide, TeacherImpulseObservation},
};

const WRAPPED_SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const TOKEN_PROGRAM_ID: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const TOKEN_2022_PROGRAM_ID: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct DetectedSwapProviderLine {
    pub kind: String,
    pub source: String,
    pub detected_at_ms: u64,
    pub signature: String,
    pub wallet: String,
    pub input_mint: String,
    pub output_mint: String,
    pub amount_raw: u64,
    pub is_native_input: bool,
    #[serde(default)]
    pub counterparty_amount_raw: Option<u64>,
    #[serde(default)]
    pub source_ix_accounts: Option<Vec<String>>,
    #[serde(default)]
    pub source_ix_data_base64: Option<String>,
    #[serde(default)]
    pub source_pump_swap_instruction: Option<String>,
    #[serde(default)]
    pub source_pump_swap_accounts: Option<Vec<String>>,
    #[serde(default)]
    pub source_quote_mint: Option<String>,
    #[serde(default)]
    pub source_quote_amount_raw: Option<u64>,
    #[serde(default)]
    pub source_slot: Option<u64>,
    #[serde(default)]
    pub source_transaction_index: Option<u64>,
}

pub fn parse_detected_swap_provider_line(
    line: &str,
    line_no: u64,
) -> Result<DetectedSwapProviderLine> {
    let value: serde_json::Value = serde_json::from_str(line)
        .with_context(|| format!("parse provider JSON line {line_no}"))?;
    if value.get("kind").and_then(|kind| kind.as_str()) != Some("swap") {
        return Ok(DetectedSwapProviderLine {
            kind: value
                .get("kind")
                .and_then(|kind| kind.as_str())
                .unwrap_or("ignored")
                .to_string(),
            source: value
                .get("source")
                .and_then(|source| source.as_str())
                .unwrap_or("unknown")
                .to_string(),
            detected_at_ms: 1,
            signature: String::new(),
            wallet: String::new(),
            input_mint: String::new(),
            output_mint: String::new(),
            amount_raw: 0,
            is_native_input: false,
            counterparty_amount_raw: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
            source_pump_swap_instruction: None,
            source_pump_swap_accounts: None,
            source_quote_mint: None,
            source_quote_amount_raw: None,
            source_slot: None,
            source_transaction_index: None,
        });
    }
    serde_json::from_value(value)
        .with_context(|| format!("parse detected swap provider line {line_no}"))
}

pub fn observation_from_detected_swap(
    line: DetectedSwapProviderLine,
) -> Result<Option<TeacherImpulseObservation>> {
    if line.kind != "swap" {
        return Ok(None);
    }

    let (side, mint, quote_sol) = if line.input_mint == WRAPPED_SOL_MINT && line.is_native_input {
        (
            ObservationSide::Buy,
            line.output_mint.clone(),
            Some(line.amount_raw as f64 / 1_000_000_000.0),
        )
    } else if line.input_mint == USDC_MINT {
        (ObservationSide::Buy, line.output_mint.clone(), None)
    } else if line.output_mint == WRAPPED_SOL_MINT {
        (ObservationSide::Sell, line.input_mint.clone(), None)
    } else {
        return Ok(None);
    };

    let source_ix_accounts = line
        .source_ix_accounts
        .clone()
        .or_else(|| line.source_pump_swap_accounts.clone());
    let token_program = infer_token_program(source_ix_accounts.as_deref());
    let raw_record = serde_json::to_value(&line).context("serialize detected swap raw record")?;
    let raw_record_identity = format!(
        "detected-swap:{}:{}:{}:{}",
        line.source,
        line.source_slot
            .map(|slot| slot.to_string())
            .unwrap_or_else(|| "unknown-slot".to_string()),
        line.source_transaction_index
            .map(|index| index.to_string())
            .unwrap_or_else(|| "unknown-tx-index".to_string()),
        line.signature
    );
    let source_transaction_index = line
        .source_transaction_index
        .and_then(|index| u32::try_from(index).ok());
    let provider_sequence = line
        .source_slot
        .zip(line.source_transaction_index)
        .map(|(slot, transaction_index)| format!("{}:{slot}:{transaction_index}", line.source));

    Ok(Some(TeacherImpulseObservation {
        source: provider_source_from_str(&line.source),
        observation_id: Some(raw_record_identity.clone()),
        raw_record_identity: Some(raw_record_identity),
        raw_record_hash: None,
        raw_record: Some(raw_record),
        source_slot: line.source_slot,
        source_transaction_index,
        provider_sequence,
        local_receive_ts_ms: line.detected_at_ms,
        decode_complete_ts_ms: Some(line.detected_at_ms),
        transaction_signature: Some(line.signature),
        mint,
        token_program,
        source_ix_data_base64: line.source_ix_data_base64,
        creation_proven: None,
        creation_ts_ms: None,
        side,
        quote_sol,
        quote_lamports: None,
        sell_quote_lamports: None,
        sell_token_amount_raw: None,
        wallet: Some(line.wallet),
        canonical_state: None,
        mayhem: Some(MayhemEvidence {
            status: MayhemStatus::Unknown,
            reason: Some("mayhem_unproven_by_detected_swap_adapter".to_string()),
            checked_at_ms: None,
        }),
        execution: None,
        extra: json!({
            "adapter": "detected_swap_provider_line",
            "inputMint": line.input_mint,
            "outputMint": line.output_mint,
            "amountRaw": line.amount_raw,
            "counterpartyAmountRaw": line.counterparty_amount_raw,
            "sourceQuoteMint": line.source_quote_mint,
            "sourceQuoteAmountRaw": line.source_quote_amount_raw,
            "sourcePumpSwapInstruction": line.source_pump_swap_instruction,
        }),
    }))
}

fn provider_source_from_str(source: &str) -> ProviderSource {
    let normalized = source.to_ascii_lowercase();
    if normalized.contains("shred") {
        ProviderSource::ShredStream
    } else if normalized.contains("rabbit") {
        ProviderSource::RabbitStream
    } else if normalized.contains("yellowstone") || normalized == "rust-sidecar" {
        ProviderSource::Yellowstone
    } else {
        ProviderSource::Unknown
    }
}

fn infer_token_program(accounts: Option<&[String]>) -> Option<String> {
    let accounts = accounts?;
    if accounts
        .iter()
        .any(|account| account == TOKEN_2022_PROGRAM_ID)
    {
        Some(TOKEN_2022_PROGRAM_ID.to_string())
    } else if accounts.iter().any(|account| account == TOKEN_PROGRAM_ID) {
        Some(TOKEN_PROGRAM_ID.to_string())
    } else {
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detected_shredstream_sol_buy_converts_to_observation() {
        let line = DetectedSwapProviderLine {
            kind: "swap".to_string(),
            source: "jito-shredstream".to_string(),
            detected_at_ms: 1_700,
            signature: "sig".to_string(),
            wallet: "wallet".to_string(),
            input_mint: WRAPPED_SOL_MINT.to_string(),
            output_mint: "mint".to_string(),
            amount_raw: 1_500_000_000,
            is_native_input: true,
            counterparty_amount_raw: None,
            source_ix_accounts: Some(vec![TOKEN_2022_PROGRAM_ID.to_string()]),
            source_ix_data_base64: Some("AQID".to_string()),
            source_pump_swap_instruction: None,
            source_pump_swap_accounts: None,
            source_quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
            source_quote_amount_raw: Some(1_500_000_000),
            source_slot: Some(42),
            source_transaction_index: Some(7),
        };

        let observation = observation_from_detected_swap(line).unwrap().unwrap();

        assert_eq!(observation.source, ProviderSource::ShredStream);
        assert_eq!(observation.side, ObservationSide::Buy);
        assert_eq!(observation.mint, "mint");
        assert_eq!(observation.quote_sol, Some(1.5));
        assert_eq!(
            observation.token_program,
            Some(TOKEN_2022_PROGRAM_ID.to_string())
        );
        assert_eq!(observation.mayhem.unwrap().status, MayhemStatus::Unknown);
    }

    #[test]
    fn detected_sell_converts_to_sell_observation_without_fake_sol_size() {
        let line = DetectedSwapProviderLine {
            kind: "swap".to_string(),
            source: "rust-sidecar".to_string(),
            detected_at_ms: 1_800,
            signature: "sig".to_string(),
            wallet: "wallet".to_string(),
            input_mint: "mint".to_string(),
            output_mint: WRAPPED_SOL_MINT.to_string(),
            amount_raw: 99,
            is_native_input: false,
            counterparty_amount_raw: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
            source_pump_swap_instruction: None,
            source_pump_swap_accounts: None,
            source_quote_mint: None,
            source_quote_amount_raw: None,
            source_slot: Some(42),
            source_transaction_index: Some(8),
        };

        let observation = observation_from_detected_swap(line).unwrap().unwrap();

        assert_eq!(observation.source, ProviderSource::Yellowstone);
        assert_eq!(observation.side, ObservationSide::Sell);
        assert_eq!(observation.mint, "mint");
        assert_eq!(observation.quote_sol, None);
    }

    #[test]
    fn non_swap_line_is_ignored() {
        let line = DetectedSwapProviderLine {
            kind: "health".to_string(),
            source: "jito-shredstream".to_string(),
            detected_at_ms: 1,
            signature: "sig".to_string(),
            wallet: "wallet".to_string(),
            input_mint: WRAPPED_SOL_MINT.to_string(),
            output_mint: "mint".to_string(),
            amount_raw: 1,
            is_native_input: true,
            counterparty_amount_raw: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
            source_pump_swap_instruction: None,
            source_pump_swap_accounts: None,
            source_quote_mint: None,
            source_quote_amount_raw: None,
            source_slot: None,
            source_transaction_index: None,
        };

        assert!(observation_from_detected_swap(line).unwrap().is_none());
    }
}
