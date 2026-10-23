//! Venue-specific Pump bonding-curve transition materialization.
//!
//! This module deliberately does not reuse the PumpSwap row schema or its
//! reserve/fee arithmetic.  It accepts only an exact Pump transition produced
//! by `decode_exact_pump_transitions` and emits an immutable, non-PnL tape row.

use anyhow::{bail, Context, Result};
use crate::kefi_pump_curve_semantics_v1::decode_exact_pump_transitions;
use serde::Serialize;
use solana_pubkey::Pubkey;
use yellowstone_grpc_proto::geyser::SubscribeUpdateTransactionInfo;

const WSOL: &str = "So11111111111111111111111111111111111111112";
const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";

/// Stable row schema for the Pump bonding-curve venue.  There are no
/// PumpSwap-only fields here and no synthesized cash-flow/PnL fields.
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct PumpCurveTransitionRow {
    pub schema: &'static str,
    pub venue: &'static str,
    pub source_instance_id: String,
    pub continuity_epoch: u64,
    pub durable_output_sequence: u64,
    pub received_at_unix_ns: u128,
    pub source_frame_sha256: String,
    pub signature: String,
    pub slot: u64,
    pub log_index: usize,
    pub instruction_coordinate: String,
    pub intrinsic_event_id: String,
    pub curve: String,
    pub mint: String,
    pub quote_mint: String,
    pub side: String,
    pub sol_amount_raw: u64,
    pub token_amount_raw: u64,
    pub virtual_sol_reserves_raw: u64,
    pub virtual_token_reserves_raw: u64,
    pub real_sol_reserves_raw: u64,
    pub real_token_reserves_raw: u64,
    pub fee_bps: u64,
    pub fee_raw: u64,
    pub creator_fee_bps: u64,
    pub creator_fee_raw: u64,
    pub exact_transition_validated: bool,
    pub pnl_available: bool,
}

pub fn pump_curve_for_mint(mint: &str) -> Result<String> {
    let mint: Pubkey = mint.parse().context("PUMP_MINT_PUBKEY")?;
    let program: Pubkey = PUMP_PROGRAM.parse().expect("valid Pump program id");
    Ok(
        Pubkey::find_program_address(&[b"bonding-curve", mint.as_ref()], &program)
            .0
            .to_string(),
    )
}

pub fn materialize_transaction(
    info: &SubscribeUpdateTransactionInfo,
    slot: u64,
    source_instance_id: &str,
    continuity_epoch: u64,
    durable_output_sequence: u64,
    received_at_unix_ns: u128,
    source_frame_sha256: &str,
) -> Result<Vec<PumpCurveTransitionRow>> {
    let _transaction = info
        .transaction
        .as_ref()
        .context("PUMP_TRANSACTION_MISSING")?;
    let signature = bs58::encode(&info.signature).into_string();
    let transitions = decode_exact_pump_transitions(info)?;
    transitions
        .into_iter()
        .map(|transition| {
            let event = transition.event;
            if event.mint == WSOL || event.mint.is_empty() || event.ix_name.is_empty() {
                bail!("PUMP_UNSUPPORTED_EVENT_IDENTITY")
            }
            if event.sol_amount_raw == 0 || event.token_amount_raw == 0 {
                bail!("PUMP_ZERO_AMOUNT_TRANSITION")
            }
            let curve = pump_curve_for_mint(&event.mint)?;
            let intrinsic_event_id = format!(
                "{}:{}:{}",
                signature, transition.log_index, transition.instruction_coordinate
            );
            Ok(PumpCurveTransitionRow {
                schema: "KEFI_FROZEN_PUMP_CURVE_TRANSITION_TAPE_ROW_V1",
                venue: "PUMP_BONDING_CURVE",
                source_instance_id: source_instance_id.to_owned(),
                continuity_epoch,
                durable_output_sequence,
                received_at_unix_ns,
                source_frame_sha256: source_frame_sha256.to_owned(),
                signature: signature.clone(),
                slot,
                log_index: transition.log_index,
                instruction_coordinate: transition.instruction_coordinate,
                intrinsic_event_id,
                curve,
                mint: event.mint,
                quote_mint: WSOL.to_owned(),
                side: event.side.to_owned(),
                sol_amount_raw: event.sol_amount_raw,
                token_amount_raw: event.token_amount_raw,
                virtual_sol_reserves_raw: event.virtual_sol_reserves_raw,
                virtual_token_reserves_raw: event.virtual_token_reserves_raw,
                real_sol_reserves_raw: event.real_sol_reserves_raw,
                real_token_reserves_raw: event.real_token_reserves_raw,
                fee_bps: event.fee_bps,
                fee_raw: event.fee_raw,
                creator_fee_bps: event.creator_fee_bps,
                creator_fee_raw: event.creator_fee_raw,
                exact_transition_validated: true,
                pnl_available: false,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use yellowstone_grpc_proto::solana::storage::confirmed_block::{
        CompiledInstruction, Message, Transaction, TransactionStatusMeta,
    };

    fn event_data() -> Vec<u8> {
        let mut raw = vec![189, 219, 127, 211, 78, 230, 97, 238];
        raw.extend_from_slice(&[1; 32]);
        raw.extend_from_slice(&7u64.to_le_bytes());
        raw.extend_from_slice(&9u64.to_le_bytes());
        raw.push(1);
        raw.extend_from_slice(&[2; 32]);
        for n in [1u64, 2, 3, 4, 5] {
            raw.extend_from_slice(&n.to_le_bytes());
        }
        raw.extend_from_slice(&[3; 32]);
        raw.extend_from_slice(&5u64.to_le_bytes());
        raw.extend_from_slice(&6u64.to_le_bytes());
        raw.extend_from_slice(&[4; 32]);
        raw.extend_from_slice(&7u64.to_le_bytes());
        raw.extend_from_slice(&8u64.to_le_bytes());
        raw.push(0);
        for n in [9u64, 10, 11, 12] {
            raw.extend_from_slice(&n.to_le_bytes());
        }
        raw.extend_from_slice(&(3u32).to_le_bytes());
        raw.extend_from_slice(b"buy");
        raw
    }

    fn transaction_info() -> SubscribeUpdateTransactionInfo {
        let program = bs58::decode(PUMP_PROGRAM).into_vec().unwrap();
        SubscribeUpdateTransactionInfo {
            signature: vec![9; 64],
            transaction: Some(Transaction {
                signatures: vec![vec![9; 64]],
                message: Some(Message {
                    account_keys: vec![vec![1; 32], vec![2; 32], program],
                    instructions: vec![CompiledInstruction {
                        program_id_index: 2,
                        accounts: vec![0, 0, 0, 0, 0, 0, 1],
                        data: vec![102, 6, 61, 18, 1, 218, 235, 234],
                    }],
                    ..Default::default()
                }),
            }),
            meta: Some(TransactionStatusMeta {
                log_messages: vec![format!("Program data: {}", STANDARD.encode(event_data()))],
                ..Default::default()
            }),
            ..Default::default()
        }
    }

    #[test]
    fn materializes_separate_curve_schema_and_identity() {
        let info = transaction_info();
        let rows = materialize_transaction(&info, 42, "source", 1, 7, 99, "frame").unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(
            rows[0].schema,
            "KEFI_FROZEN_PUMP_CURVE_TRANSITION_TAPE_ROW_V1"
        );
        assert_eq!(rows[0].venue, "PUMP_BONDING_CURVE");
        assert_eq!(rows[0].quote_mint, WSOL);
        assert!(rows[0].exact_transition_validated);
        assert!(!rows[0].pnl_available);
        assert_eq!(rows[0].curve, pump_curve_for_mint(&rows[0].mint).unwrap());
    }

    #[test]
    fn failed_transaction_is_rejected_before_materialization() {
        let mut info = transaction_info();
        info.meta.as_mut().unwrap().err = Some(Default::default());
        assert_eq!(
            materialize_transaction(&info, 42, "source", 1, 7, 99, "frame")
                .unwrap_err()
                .to_string(),
            "FAILED_PUMP_TRANSACTION"
        );
    }
}
