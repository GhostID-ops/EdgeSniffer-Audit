//! Exact Pump bonding-curve TradeEvent decoder.
//!
//! This adapter is intentionally separate from the PumpSwap decoder: a Pump
//! curve trade has a different state model and must never be passed through
//! PumpSwap fee/reserve assumptions.  Layout is bound to Pump's public IDL.

use anyhow::{bail, Result};
use base64::{engine::general_purpose::STANDARD, Engine as _};

pub const PUMP_PROGRAM: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
pub const TRADE_EVENT_DISC: [u8; 8] = [189, 219, 127, 211, 78, 230, 97, 238];
const BUY: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const BUY_EXACT_SOL_IN: [u8; 8] = [56, 252, 116, 8, 158, 223, 205, 95];
const BUY_V2: [u8; 8] = [184, 23, 238, 97, 103, 197, 211, 61];
const BUY_EXACT_QUOTE_IN_V2: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];
const SELL: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PumpTradeEvent {
    pub mint: String,
    pub user: String,
    pub side: &'static str,
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
    pub ix_name: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ExactPumpTransition {
    pub log_index: usize,
    pub instruction_coordinate: String,
    pub event: PumpTradeEvent,
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct PumpInvocation {
    coordinate: String,
    side: &'static str,
    mint: String,
    user: String,
}

fn u64le(raw: &[u8], offset: &mut usize) -> Result<u64> {
    let bytes = raw
        .get(*offset..*offset + 8)
        .ok_or_else(|| anyhow::anyhow!("PUMP_EVENT_TRUNCATED"))?;
    *offset += 8;
    Ok(u64::from_le_bytes(bytes.try_into().unwrap()))
}
fn bool_at(raw: &[u8], offset: &mut usize) -> Result<bool> {
    let value = *raw
        .get(*offset)
        .ok_or_else(|| anyhow::anyhow!("PUMP_EVENT_TRUNCATED"))?;
    *offset += 1;
    match value {
        0 => Ok(false),
        1 => Ok(true),
        _ => bail!("PUMP_EVENT_INVALID_BOOL"),
    }
}
fn key(raw: &[u8], offset: &mut usize) -> Result<String> {
    let bytes = raw
        .get(*offset..*offset + 32)
        .ok_or_else(|| anyhow::anyhow!("PUMP_EVENT_TRUNCATED"))?;
    *offset += 32;
    Ok(bs58::encode(bytes).into_string())
}
fn string(raw: &[u8], offset: &mut usize) -> Result<String> {
    let len = raw
        .get(*offset..*offset + 4)
        .ok_or_else(|| anyhow::anyhow!("PUMP_EVENT_TRUNCATED"))?;
    *offset += 4;
    let len = u32::from_le_bytes(len.try_into().unwrap()) as usize;
    let bytes = raw
        .get(*offset..*offset + len)
        .ok_or_else(|| anyhow::anyhow!("PUMP_EVENT_TRUNCATED"))?;
    *offset += len;
    Ok(std::str::from_utf8(bytes)
        .map_err(|_| anyhow::anyhow!("PUMP_EVENT_INVALID_IX_NAME"))?
        .to_owned())
}

/// Decode one `Program data:` TradeEvent.  Later IDL extension fields are not
/// silently guessed: only the stable prefix through `ixName` is consumed.
pub fn decode_trade_event_base64(encoded: &str) -> Result<Option<PumpTradeEvent>> {
    let raw = match STANDARD.decode(encoded.trim()) {
        Ok(raw) => raw,
        Err(_) => return Ok(None),
    };
    if !raw.starts_with(&TRADE_EVENT_DISC) {
        return Ok(None);
    }
    let mut o = 8;
    let mint = key(&raw, &mut o)?;
    let sol_amount_raw = u64le(&raw, &mut o)?;
    let token_amount_raw = u64le(&raw, &mut o)?;
    let is_buy = bool_at(&raw, &mut o)?;
    let user = key(&raw, &mut o)?;
    let _timestamp = u64le(&raw, &mut o)?;
    let virtual_sol_reserves_raw = u64le(&raw, &mut o)?;
    let virtual_token_reserves_raw = u64le(&raw, &mut o)?;
    let real_sol_reserves_raw = u64le(&raw, &mut o)?;
    let real_token_reserves_raw = u64le(&raw, &mut o)?;
    let _fee_recipient = key(&raw, &mut o)?;
    let fee_bps = u64le(&raw, &mut o)?;
    let fee_raw = u64le(&raw, &mut o)?;
    let _creator = key(&raw, &mut o)?;
    let creator_fee_bps = u64le(&raw, &mut o)?;
    let creator_fee_raw = u64le(&raw, &mut o)?;
    let _track_volume = bool_at(&raw, &mut o)?;
    let _unclaimed = u64le(&raw, &mut o)?;
    let _claimed = u64le(&raw, &mut o)?;
    let _volume = u64le(&raw, &mut o)?;
    let _updated = u64le(&raw, &mut o)?;
    let ix_name = string(&raw, &mut o)?;
    Ok(Some(PumpTradeEvent {
        mint,
        user,
        side: if is_buy { "BUY" } else { "SELL" },
        sol_amount_raw,
        token_amount_raw,
        virtual_sol_reserves_raw,
        virtual_token_reserves_raw,
        real_sol_reserves_raw,
        real_token_reserves_raw,
        fee_bps,
        fee_raw,
        creator_fee_bps,
        creator_fee_raw,
        ix_name,
    }))
}

/// Extract every Pump TradeEvent from full transaction metadata.  This does
/// not infer an instruction path: callers must separately bind each event to
/// one Pump invocation before treating it as an exact transition.
pub fn decode_trade_events(
    meta: &yellowstone_grpc_proto::solana::storage::confirmed_block::TransactionStatusMeta,
) -> Result<Vec<(usize, PumpTradeEvent)>> {
    meta.log_messages
        .iter()
        .enumerate()
        .filter_map(|(index, log)| {
            log.strip_prefix("Program data: ")
                .map(|encoded| (index, encoded))
        })
        .map(|(index, encoded)| {
            decode_trade_event_base64(encoded).map(|event| event.map(|event| (index, event)))
        })
        .filter_map(|result| match result {
            Ok(Some(event)) => Some(Ok(event)),
            Ok(None) => None,
            Err(error) => Some(Err(error)),
        })
        .collect()
}

fn bind_events_to_invocations(
    events: Vec<(usize, PumpTradeEvent)>,
    invocations: &[PumpInvocation],
) -> Result<Vec<ExactPumpTransition>> {
    if invocations.len() != events.len() {
        bail!("PUMP_EVENT_INSTRUCTION_CARDINALITY_MISMATCH")
    }
    let mut used = std::collections::BTreeSet::new();
    let mut out = Vec::new();
    for (log_index, event) in events {
        let matches: Vec<_> = invocations
            .iter()
            .filter(|invocation| {
                invocation.side == event.side
                    && invocation.mint == event.mint
                    && invocation.user == event.user
            })
            .collect();
        if matches.len() != 1 {
            bail!("PUMP_EVENT_INSTRUCTION_MAPPING_AMBIGUOUS")
        }
        let invocation = matches[0];
        if !used.insert(invocation.coordinate.clone()) {
            bail!("PUMP_EVENT_INSTRUCTION_MAPPING_NOT_ONE_TO_ONE")
        }
        out.push(ExactPumpTransition {
            log_index,
            instruction_coordinate: invocation.coordinate.clone(),
            event,
        });
    }
    Ok(out)
}

/// Bind each TradeEvent to exactly one compiled Pump invocation.  Account
/// positions are the canonical Pump buy/sell mint (2) and signer (6); a
/// routed or changed layout is deliberately rejected rather than guessed.
pub fn decode_exact_pump_transitions(
    info: &yellowstone_grpc_proto::geyser::SubscribeUpdateTransactionInfo,
) -> Result<Vec<ExactPumpTransition>> {
    let tx = info
        .transaction
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("PUMP_TRANSACTION_MISSING"))?;
    let message = tx
        .message
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("PUMP_MESSAGE_MISSING"))?;
    let meta = info
        .meta
        .as_ref()
        .ok_or_else(|| anyhow::anyhow!("PUMP_FULL_META_MISSING"))?;
    if meta.err.is_some() {
        bail!("FAILED_PUMP_TRANSACTION")
    }
    let mut keys: Vec<String> = message
        .account_keys
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect();
    keys.extend(
        meta.loaded_writable_addresses
            .iter()
            .chain(meta.loaded_readonly_addresses.iter())
            .map(|key| bs58::encode(key).into_string()),
    );
    let mut invocations: Vec<PumpInvocation> = Vec::new();
    let mut visit = |coordinate: String, program: u32, accounts: &[u8], data: &[u8]| {
        if keys.get(program as usize).map(String::as_str) != Some(PUMP_PROGRAM) || data.len() < 8 {
            return;
        }
        let side = if data.starts_with(&BUY)
            || data.starts_with(&BUY_EXACT_SOL_IN)
            || data.starts_with(&BUY_V2)
            || data.starts_with(&BUY_EXACT_QUOTE_IN_V2)
        {
            "BUY"
        } else if data.starts_with(&SELL) {
            "SELL"
        } else {
            return;
        };
        let (Some(mint), Some(user)) = (
            accounts.get(2).and_then(|i| keys.get(*i as usize)),
            accounts.get(6).and_then(|i| keys.get(*i as usize)),
        ) else {
            return;
        };
        invocations.push(PumpInvocation {
            coordinate,
            side,
            mint: mint.clone(),
            user: user.clone(),
        });
    };
    for (index, ix) in message.instructions.iter().enumerate() {
        visit(
            format!("outer:{index:05}"),
            ix.program_id_index,
            &ix.accounts,
            &ix.data,
        );
    }
    for group in &meta.inner_instructions {
        for (index, ix) in group.instructions.iter().enumerate() {
            if let Some(stack) = ix.stack_height {
                visit(
                    format!("outer:{:05}/inner:{index:05}/stack:{stack:03}", group.index),
                    ix.program_id_index,
                    &ix.accounts,
                    &ix.data,
                );
            }
        }
    }
    bind_events_to_invocations(decode_trade_events(meta)?, &invocations)
}

#[cfg(test)]
mod tests {
    use super::*;
    use yellowstone_grpc_proto::geyser::SubscribeUpdateTransactionInfo;
    use yellowstone_grpc_proto::solana::storage::confirmed_block::{
        CompiledInstruction, InnerInstruction, InnerInstructions, Message, Transaction,
        TransactionError, TransactionStatusMeta,
    };
    #[test]
    fn decodes_stable_trade_event_prefix() {
        let mut raw = TRADE_EVENT_DISC.to_vec();
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
        let decoded = decode_trade_event_base64(&STANDARD.encode(raw))
            .unwrap()
            .unwrap();
        assert_eq!(decoded.side, "BUY");
        assert_eq!(decoded.sol_amount_raw, 7);
        assert_eq!(decoded.ix_name, "buy");
    }
    #[test]
    fn rejects_truncated_recognized_event() {
        assert!(decode_trade_event_base64(&STANDARD.encode(TRADE_EVENT_DISC)).is_err());
    }

    fn event(side: &'static str) -> PumpTradeEvent {
        PumpTradeEvent {
            mint: "mint".into(),
            user: "user".into(),
            side,
            sol_amount_raw: 1,
            token_amount_raw: 1,
            virtual_sol_reserves_raw: 1,
            virtual_token_reserves_raw: 1,
            real_sol_reserves_raw: 1,
            real_token_reserves_raw: 1,
            fee_bps: 100,
            fee_raw: 1,
            creator_fee_bps: 0,
            creator_fee_raw: 0,
            ix_name: "buy".into(),
        }
    }

    fn event_data() -> Vec<u8> {
        let mut raw = TRADE_EVENT_DISC.to_vec();
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

    fn info(program_in_loaded_addresses: bool, two_inner: bool) -> SubscribeUpdateTransactionInfo {
        let program = bs58::decode(PUMP_PROGRAM).into_vec().unwrap();
        let mut account_keys = vec![vec![1; 32], vec![2; 32]];
        if !program_in_loaded_addresses {
            account_keys.push(program.clone());
        }
        let program_index = 2;
        let instruction = CompiledInstruction {
            program_id_index: program_index,
            accounts: vec![0, 0, 0, 0, 0, 0, 1],
            data: BUY.to_vec(),
        };
        let mut meta = TransactionStatusMeta {
            log_messages: vec![format!("Program data: {}", STANDARD.encode(event_data()))],
            ..Default::default()
        };
        if program_in_loaded_addresses {
            meta.loaded_writable_addresses.push(program);
        }
        if two_inner {
            meta.inner_instructions.push(InnerInstructions {
                index: 0,
                instructions: vec![
                    InnerInstruction {
                        program_id_index: program_index,
                        accounts: instruction.accounts.clone(),
                        data: BUY.to_vec(),
                        stack_height: Some(1),
                    },
                    InnerInstruction {
                        program_id_index: program_index,
                        accounts: instruction.accounts.clone(),
                        data: BUY.to_vec(),
                        stack_height: Some(2),
                    },
                ],
            });
        }
        SubscribeUpdateTransactionInfo {
            signature: vec![9; 64],
            transaction: Some(Transaction {
                signatures: vec![vec![9; 64]],
                message: Some(Message {
                    account_keys,
                    instructions: if two_inner { vec![] } else { vec![instruction] },
                    ..Default::default()
                }),
            }),
            meta: Some(meta),
            ..Default::default()
        }
    }

    #[test]
    fn rejects_instruction_without_event() {
        let invocations = vec![PumpInvocation {
            coordinate: "outer:00000".into(),
            side: "BUY",
            mint: "mint".into(),
            user: "user".into(),
        }];
        assert_eq!(
            bind_events_to_invocations(Vec::new(), &invocations)
                .unwrap_err()
                .to_string(),
            "PUMP_EVENT_INSTRUCTION_CARDINALITY_MISMATCH"
        );
    }

    #[test]
    fn rejects_event_without_instruction() {
        assert_eq!(
            bind_events_to_invocations(vec![(0, event("BUY"))], &[])
                .unwrap_err()
                .to_string(),
            "PUMP_EVENT_INSTRUCTION_CARDINALITY_MISMATCH"
        );
    }

    #[test]
    fn rejects_multiple_matching_instructions() {
        let invocations = vec![
            PumpInvocation {
                coordinate: "outer:00000".into(),
                side: "BUY",
                mint: "mint".into(),
                user: "user".into(),
            },
            PumpInvocation {
                coordinate: "outer:00001".into(),
                side: "BUY",
                mint: "mint".into(),
                user: "user".into(),
            },
        ];
        assert_eq!(
            bind_events_to_invocations(vec![(0, event("BUY"))], &invocations)
                .unwrap_err()
                .to_string(),
            "PUMP_EVENT_INSTRUCTION_CARDINALITY_MISMATCH"
        );
    }

    #[test]
    fn resolves_loaded_program_address() {
        let transitions = decode_exact_pump_transitions(&info(true, false)).unwrap();
        assert_eq!(transitions.len(), 1);
        assert_eq!(transitions[0].instruction_coordinate, "outer:00000");
    }

    #[test]
    fn rejects_multiple_inner_instructions_for_one_event() {
        assert_eq!(
            decode_exact_pump_transitions(&info(false, true))
                .unwrap_err()
                .to_string(),
            "PUMP_EVENT_INSTRUCTION_CARDINALITY_MISMATCH"
        );
    }

    #[test]
    fn rejects_failed_transaction() {
        let mut transaction = info(false, false);
        transaction.meta.as_mut().unwrap().err = Some(TransactionError { err: vec![1] });
        assert_eq!(
            decode_exact_pump_transitions(&transaction)
                .unwrap_err()
                .to_string(),
            "FAILED_PUMP_TRANSACTION"
        );
    }
}
