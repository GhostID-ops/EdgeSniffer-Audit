//! Normalize retained Yellowstone protobuf transactions for the offline 2CQ
//! state-plane adapter. This is deliberately not a collector and never assigns
//! finality or arrival time; its caller supplies those only from independent
//! evidence.

use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use prost::Message;
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs;
use std::io::{self, BufRead, Write};
use yellowstone_grpc_proto::geyser::{subscribe_update::UpdateOneof, SubscribeUpdate};

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct InputRow {
    signature: String,
    raw_base64: String,
}

fn instruction_json(instruction: &yellowstone_grpc_proto::prelude::CompiledInstruction) -> Value {
    json!({
        "programIdIndex": instruction.program_id_index,
        "accounts": instruction.accounts,
        "data": bs58::encode(&instruction.data).into_string(),
    })
}

fn inner_instruction_json(
    instruction: &yellowstone_grpc_proto::prelude::InnerInstruction,
) -> Value {
    json!({
        "programIdIndex": instruction.program_id_index,
        "accounts": instruction.accounts,
        "data": bs58::encode(&instruction.data).into_string(),
        "stackHeight": instruction.stack_height,
    })
}

fn normalize(
    row: InputRow,
    target_mints: &HashSet<String>,
    all_transactions: bool,
) -> Result<Option<Value>, String> {
    let raw = BASE64_STANDARD
        .decode(row.raw_base64)
        .map_err(|error| format!("BASE64_DECODE:{error}"))?;
    let update = SubscribeUpdate::decode(raw.as_slice())
        .map_err(|error| format!("PROTOBUF_DECODE:{error}"))?;
    let UpdateOneof::Transaction(update_tx) = update
        .update_oneof
        .ok_or_else(|| "NOT_TRANSACTION_UPDATE".to_string())?
    else {
        return Err("NOT_TRANSACTION_UPDATE".to_string());
    };
    let info = update_tx
        .transaction
        .ok_or_else(|| "MISSING_TRANSACTION_INFO".to_string())?;
    let actual_signature = bs58::encode(&info.signature).into_string();
    if actual_signature != row.signature {
        return Err("SIGNATURE_MISMATCH".to_string());
    }
    let transaction = info
        .transaction
        .ok_or_else(|| "MISSING_TRANSACTION_BODY".to_string())?;
    let message = transaction
        .message
        .ok_or_else(|| "MISSING_MESSAGE".to_string())?;
    let meta = info
        .meta
        .ok_or_else(|| "MISSING_TRANSACTION_META".to_string())?;
    let account_keys = message
        .account_keys
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect::<Vec<_>>();
    let loaded_writable = meta
        .loaded_writable_addresses
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect::<Vec<_>>();
    let loaded_readonly = meta
        .loaded_readonly_addresses
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect::<Vec<_>>();
    if !all_transactions
        && !account_keys
            .iter()
            .chain(loaded_writable.iter())
            .chain(loaded_readonly.iter())
            .any(|key| target_mints.contains(key))
    {
        return Ok(None);
    }
    let instructions = message
        .instructions
        .iter()
        .map(instruction_json)
        .collect::<Vec<_>>();
    let inner_instructions = meta
        .inner_instructions
        .iter()
        .map(|group| {
            json!({
                "index": group.index,
                "instructions": group.instructions.iter().map(inner_instruction_json).collect::<Vec<_>>(),
            })
        })
        .collect::<Vec<_>>();
    // These balances are present in the retained protobuf. Preserve the
    // concrete rows rather than a count: the exact Pump event decoder needs
    // mint, owner, account index, and raw amount to establish token deltas.
    let token_balances =
        |balances: &[yellowstone_grpc_proto::solana::storage::confirmed_block::TokenBalance]| {
            balances
                .iter()
                .map(|balance| {
                    let amount = balance.ui_token_amount.as_ref();
                    json!({
                        "accountIndex": balance.account_index,
                        "mint": balance.mint,
                        "owner": balance.owner,
                        "programId": balance.program_id,
                        "uiTokenAmount": amount.map(|value| json!({
                            "uiAmount": value.ui_amount,
                            "decimals": value.decimals,
                            "amount": value.amount,
                            "uiAmountString": value.ui_amount_string,
                        })),
                    })
                })
                .collect::<Vec<_>>()
        };
    Ok(Some(json!({
        "signature": actual_signature,
        "slot": update_tx.slot,
        "transactionIndex": info.index,
        "successfulAtReceipt": meta.err.is_none(),
        "transaction": {
            "message": {
                "accountKeys": account_keys,
                "instructions": instructions,
            }
        },
        "meta": {
            "err": if meta.err.is_none() { Value::Null } else { json!("receipt_status_not_authoritative") },
            "preBalances": meta.pre_balances,
            "postBalances": meta.post_balances,
            "preTokenBalances": token_balances(&meta.pre_token_balances),
            "postTokenBalances": token_balances(&meta.post_token_balances),
            "logMessages": meta.log_messages,
            "innerInstructions": inner_instructions,
            "loadedAddresses": {
                "writable": loaded_writable,
                "readonly": loaded_readonly,
            }
        }
    })))
}

fn main() {
    let mut arguments = std::env::args().skip(1);
    let Some(flag) = arguments.next() else {
        eprintln!("usage: yellowstone_state_plane_normalizer --target-mints PATH | --all");
        std::process::exit(2);
    };
    let (target_mints, all_transactions) = if flag == "--all" && arguments.next().is_none() {
        (HashSet::new(), true)
    } else {
        let Some(path) = arguments.next() else {
            eprintln!("usage: yellowstone_state_plane_normalizer --target-mints PATH | --all");
            std::process::exit(2);
        };
        if flag != "--target-mints" || arguments.next().is_some() {
            eprintln!("usage: yellowstone_state_plane_normalizer --target-mints PATH | --all");
            std::process::exit(2);
        }
        let targets = match fs::read_to_string(path) {
            Ok(value) => value
                .lines()
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
                .collect::<HashSet<_>>(),
            Err(error) => {
                eprintln!("target mint file: {error}");
                std::process::exit(2);
            }
        };
        (targets, false)
    };
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout().lock());
    for line in stdin.lock().lines() {
        let value = match line {
            Ok(value) => value,
            Err(error) => {
                let _ = writeln!(
                    stdout,
                    "{}",
                    json!({"ok": false, "error": format!("STDIN:{error}")})
                );
                continue;
            }
        };
        let result = serde_json::from_str::<InputRow>(&value)
            .map_err(|error| format!("INPUT_JSON:{error}"))
            .and_then(|row| normalize(row, &target_mints, all_transactions));
        match result {
            Ok(Some(normalized)) => {
                writeln!(stdout, "{}", json!({"ok": true, "normalized": normalized})).unwrap()
            }
            Ok(None) => {}
            Err(error) => writeln!(stdout, "{}", json!({"ok": false, "error": error})).unwrap(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use yellowstone_grpc_proto::{
        geyser::{
            subscribe_update::UpdateOneof, SubscribeUpdateTransaction,
            SubscribeUpdateTransactionInfo,
        },
        solana::storage::confirmed_block::{
            CompiledInstruction, Message as SolanaMessage, Transaction, TransactionStatusMeta,
        },
    };

    #[test]
    fn normalizes_loaded_address_cpi_shape() {
        let signature = vec![7; 64];
        let expected_signature = bs58::encode(&signature).into_string();
        let update = SubscribeUpdate {
            filters: vec![],
            created_at: None,
            update_oneof: Some(UpdateOneof::Transaction(SubscribeUpdateTransaction {
                slot: 44,
                transaction: Some(SubscribeUpdateTransactionInfo {
                    signature,
                    is_vote: false,
                    index: 3,
                    transaction: Some(Transaction {
                        signatures: vec![vec![7; 64]],
                        message: Some(SolanaMessage {
                            account_keys: vec![vec![1; 32]],
                            instructions: vec![CompiledInstruction {
                                program_id_index: 1,
                                accounts: vec![0],
                                data: vec![9; 8],
                            }],
                            ..Default::default()
                        }),
                    }),
                    meta: Some(TransactionStatusMeta {
                        loaded_readonly_addresses: vec![vec![2; 32]],
                        log_messages: vec!["Program log: test".to_string()],
                        ..Default::default()
                    }),
                }),
            })),
        };
        let mut raw = Vec::new();
        update.encode(&mut raw).unwrap();
        let targets = [bs58::encode(vec![2; 32]).into_string()]
            .into_iter()
            .collect();
        let value = normalize(
            InputRow {
                signature: expected_signature,
                raw_base64: BASE64_STANDARD.encode(raw),
            },
            &targets,
            false,
        )
        .unwrap()
        .unwrap();
        assert_eq!(value["slot"], 44);
        assert_eq!(value["transactionIndex"], 3);
        assert_eq!(
            value["meta"]["loadedAddresses"]["readonly"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            value["transaction"]["message"]["instructions"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
    }
}
