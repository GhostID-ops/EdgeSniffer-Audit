use std::{
    env,
    str::FromStr,
    time::{Duration, Instant},
};

use anyhow::{anyhow, bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use futures::{stream::FuturesUnordered, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use solana_compute_budget_interface::ComputeBudgetInstruction;
use solana_hash::Hash;
use solana_instruction::{AccountMeta, Instruction};
use solana_keypair::Keypair;
use solana_message::{v0, VersionedMessage};
use solana_pubkey::Pubkey;
use solana_signer::Signer;
use solana_system_interface::instruction as system_instruction;
use solana_transaction::versioned::VersionedTransaction;
use tokio::io::{self, AsyncBufReadExt, BufReader};

const PACKET_DATA_SIZE: usize = 1232;
const WRAPPED_SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const PUMP_FUN_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMP_FUN_FEE_PROGRAM_ID: &str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ";
const PUMP_FUN_FEE_RECIPIENT: &str = "CebN5WGQ4jvEPvsVU4EoHEpgzq1VV7AbicfhtW4xC9iM";
const PUMP_FUN_BUYBACK_FEE_RECIPIENT: &str = "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD";
const TOKEN_PROGRAM_ID: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const TOKEN_2022_PROGRAM_ID: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const ASSOCIATED_TOKEN_PROGRAM_ID: &str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";

const BUY_DISCRIMINATOR: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const BUY_EXACT_SOL_IN_DISCRIMINATOR: [u8; 8] = [56, 252, 116, 8, 158, 223, 205, 95];
const BUY_V2_DISCRIMINATOR: [u8; 8] = [184, 23, 238, 97, 103, 197, 211, 61];
const BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];

const PUMP_FUN_BUY_WRITABLE_FLAGS: [bool; 16] = [
    false, true, false, true, true, true, true, false, false, true, false, false, false, true,
    false, false,
];
const PUMP_FUN_BUY_V2_WRITABLE_FLAGS: [bool; 27] = [
    false, false, false, false, false, false, true, true, true, true, true, true, true, true,
    true, true, true, true, false, false, true, true, false, false, false, false, false,
];
const PUMP_FUN_BUY_V2_ACCOUNT_COUNT: usize = 27;

const JITO_TIP_ACCOUNTS: [&str; 8] = [
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
];

const HELIUS_TIP_ACCOUNTS: [&str; 11] = [
    "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta",
    "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
    "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ",
    "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or",
    "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn",
    "2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD",
    "D1Mc6j9xQWgR1o1Z7yU5nVVXFQiAYx7FG9AW1aVfwrUM",
    "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE",
    "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
    "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF",
    "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey",
];

#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
enum ExecutorCommand {
    ExecutePumpfunBuy(ExecutePumpfunBuyCommand),
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutePumpfunBuyCommand {
    id: String,
    mode: String,
    route: String,
    mint: String,
    sol_in_lamports: u64,
    tokens_out_raw: Option<u64>,
    slippage_bps: u64,
    exact_sol_in: bool,
    source_ix_accounts: Option<Vec<String>>,
    source_ix_data_base64: Option<String>,
    bonding_curve: Option<String>,
    creator: Option<String>,
    token_program_id: Option<String>,
    blockhash: String,
    compute_unit_limit: u32,
    priority_fee_micro_lamports: u64,
    jito_tip_lamports: u64,
    helius_tip_lamports: u64,
    use_jito_direct: bool,
    use_jito_fallback: bool,
    sender_rpc_fallback_enabled: bool,
    execution_multi_send_enabled: bool,
    jito_send_mode: String,
    jito_transaction_urls: Vec<String>,
    jito_bundle_urls: Vec<String>,
    sender_endpoint_url: String,
    rpc_fallback_url: String,
    jito_auth_uuid: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct ExecutorResponse {
    kind: &'static str,
    id: String,
    ok: bool,
    signature: Option<String>,
    route: Option<String>,
    sender_path: Option<String>,
    sol_in_lamports: Option<u64>,
    tokens_out_raw: Option<u64>,
    elapsed_ms: u128,
    build_ms: Option<u128>,
    broadcast_ms: Option<u128>,
    tx_bytes: Option<usize>,
    error: Option<String>,
}

#[derive(Debug, Deserialize)]
struct JsonRpcResponse {
    result: Option<Value>,
    error: Option<JsonRpcError>,
}

#[derive(Debug, Deserialize)]
struct JsonRpcError {
    code: Option<i64>,
    message: Option<String>,
}

#[derive(Clone)]
struct BroadcastCandidate {
    name: String,
    url: String,
    method: BroadcastMethod,
}

#[derive(Clone)]
enum BroadcastMethod {
    JitoTransaction,
    JitoBundle,
    HeliusSender,
    RpcFallback,
}

struct BuiltTx {
    tx: VersionedTransaction,
    signature: String,
    tokens_out_raw: u64,
    bytes: usize,
    build_ms: u128,
}

pub fn start_executor_stdin() {
    tokio::spawn(async move {
        let stdin = BufReader::new(io::stdin());
        let mut lines = stdin.lines();
        eprintln!("rust-sidecar: Rust executor command channel ready");
        loop {
            match lines.next_line().await {
                Ok(Some(line)) => {
                    if line.trim().is_empty() {
                        continue;
                    }
                    tokio::spawn(async move {
                        let response = handle_line(&line).await;
                        match serde_json::to_string(&response) {
                            Ok(json) => println!("{json}"),
                            Err(err) => eprintln!("rust-sidecar: executor response serialization failed: {err}"),
                        }
                    });
                }
                Ok(None) => break,
                Err(err) => {
                    eprintln!("rust-sidecar: executor stdin error: {err}");
                    break;
                }
            }
        }
    });
}

async fn handle_line(line: &str) -> ExecutorResponse {
    let started = Instant::now();
    let id = extract_id(line).unwrap_or_else(|| "unknown".to_string());
    match serde_json::from_str::<ExecutorCommand>(line) {
        Ok(ExecutorCommand::ExecutePumpfunBuy(cmd)) => {
            let id = cmd.id.clone();
            match execute_pumpfun_buy(cmd, started).await {
                Ok(mut response) => {
                    response.id = id;
                    response
                }
                Err(err) => ExecutorResponse {
                    kind: "execResult",
                    id,
                    ok: false,
                    signature: None,
                    route: None,
                    sender_path: None,
                    sol_in_lamports: None,
                    tokens_out_raw: None,
                    elapsed_ms: started.elapsed().as_millis(),
                    build_ms: None,
                    broadcast_ms: None,
                    tx_bytes: None,
                    error: Some(err.to_string()),
                },
            }
        }
        Err(err) => ExecutorResponse {
            kind: "execResult",
            id,
            ok: false,
            signature: None,
            route: None,
            sender_path: None,
            sol_in_lamports: None,
            tokens_out_raw: None,
            elapsed_ms: started.elapsed().as_millis(),
            build_ms: None,
            broadcast_ms: None,
            tx_bytes: None,
            error: Some(format!("invalid executor command: {err}")),
        },
    }
}

fn extract_id(line: &str) -> Option<String> {
    serde_json::from_str::<Value>(line)
        .ok()
        .and_then(|v| v.get("id").and_then(Value::as_str).map(str::to_string))
}

async fn execute_pumpfun_buy(cmd: ExecutePumpfunBuyCommand, started: Instant) -> Result<ExecutorResponse> {
    let built = build_pumpfun_buy_tx(&cmd)?;
    let broadcast_started = Instant::now();
    let sender_path = broadcast_signed_tx(&cmd, &built.tx, &built.signature).await?;
    Ok(ExecutorResponse {
        kind: "execResult",
        id: cmd.id,
        ok: true,
        signature: Some(built.signature),
        route: Some(cmd.route),
        sender_path: Some(sender_path),
        sol_in_lamports: Some(cmd.sol_in_lamports),
        tokens_out_raw: Some(built.tokens_out_raw),
        elapsed_ms: started.elapsed().as_millis(),
        build_ms: Some(built.build_ms),
        broadcast_ms: Some(broadcast_started.elapsed().as_millis()),
        tx_bytes: Some(built.bytes),
        error: None,
    })
}

fn build_pumpfun_buy_tx(cmd: &ExecutePumpfunBuyCommand) -> Result<BuiltTx> {
    let started = Instant::now();
    let private_key = env::var("PRIVATE_KEY_BASE58").context("PRIVATE_KEY_BASE58 is not set")?;
    let keypair_bytes = bs58::decode(private_key.trim())
        .into_vec()
        .context("PRIVATE_KEY_BASE58 is not valid base58")?;
    let keypair = Keypair::from_bytes(&keypair_bytes).context("PRIVATE_KEY_BASE58 is not a Solana keypair")?;
    let user = keypair.pubkey();
    let mint = parse_pubkey(&cmd.mint, "mint")?;
    let token_program_id = token_program_for_command(cmd)?;
    let blockhash = Hash::from_str(&cmd.blockhash).context("invalid blockhash")?;

    let mut instructions = Vec::with_capacity(6);
    if cmd.compute_unit_limit > 0 {
        instructions.push(ComputeBudgetInstruction::set_compute_unit_limit(
            cmd.compute_unit_limit.min(1_400_000),
        ));
    }
    instructions.push(ComputeBudgetInstruction::set_compute_unit_price(
        cmd.priority_fee_micro_lamports,
    ));
    instructions.push(create_ata_idempotent_ix(&user, &user, &mint, &token_program_id)?);
    instructions.push(build_pumpfun_buy_ix(cmd, &user, &mint, &token_program_id)?);

    let tip_sets = tip_instruction_sets(cmd, &user)?;
    let mut last_error: Option<anyhow::Error> = None;
    for tips in tip_sets {
        let mut all_ixs = instructions.clone();
        all_ixs.extend(tips);
        match compile_and_sign(&keypair, blockhash, &all_ixs) {
            Ok((tx, bytes)) if bytes <= PACKET_DATA_SIZE => {
                let signature = tx
                    .signatures
                    .first()
                    .ok_or_else(|| anyhow!("signed transaction has no signature"))?
                    .to_string();
                let tokens_out_raw = estimate_tokens_out(cmd)?;
                return Ok(BuiltTx {
                    tx,
                    signature,
                    tokens_out_raw,
                    bytes,
                    build_ms: started.elapsed().as_millis(),
                });
            }
            Ok((_tx, bytes)) => {
                last_error = Some(anyhow!("transaction too large: {bytes}B > {PACKET_DATA_SIZE}B"));
            }
            Err(err) => {
                last_error = Some(err);
            }
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow!("no tip set could build a transaction")))
}

fn compile_and_sign(keypair: &Keypair, blockhash: Hash, instructions: &[Instruction]) -> Result<(VersionedTransaction, usize)> {
    let payer = keypair.pubkey();
    let message = v0::Message::try_compile(&payer, instructions, &[], blockhash)
        .context("compile v0 message failed")?;
    let tx = VersionedTransaction::try_new(VersionedMessage::V0(message), &[keypair])
        .context("sign v0 transaction failed")?;
    let bytes = bincode::serialize(&tx).context("serialize transaction failed")?.len();
    Ok((tx, bytes))
}

fn build_pumpfun_buy_ix(
    cmd: &ExecutePumpfunBuyCommand,
    user: &Pubkey,
    mint: &Pubkey,
    token_program_id: &Pubkey,
) -> Result<Instruction> {
    if cmd.mode == "source-replay" {
        let source_accounts = parse_source_accounts(cmd)?;
        let source_data = decode_source_data(cmd)?;
        let scaled = scale_source_buy_data(&source_data, cmd.sol_in_lamports, cmd.slippage_bps)?;
        let exact_sol_in = scaled.exact_sol_in;
        return build_source_replay_buy_ix(
            &source_accounts,
            &source_data_for_scaled(&source_data, &scaled),
            user,
            mint,
            token_program_id,
            exact_sol_in,
        );
    }

    let amount = if cmd.exact_sol_in {
        let tokens = cmd.tokens_out_raw.context("direct rust buy requires tokensOutRaw")?;
        tokens
            .saturating_mul(10_000u64.saturating_sub(cmd.slippage_bps.min(10_000)))
            / 10_000
    } else {
        cmd.tokens_out_raw.context("direct rust buy requires tokensOutRaw")?
    };
    let max_sol_cost = if cmd.exact_sol_in {
        cmd.sol_in_lamports
    } else {
        cmd.sol_in_lamports
            .saturating_mul(10_000u64.saturating_add(cmd.slippage_bps))
            / 10_000
    };
    let data = encode_buy_args(cmd, amount.max(1), max_sol_cost);

    if let Some(source_ix_accounts) = &cmd.source_ix_accounts {
        let source_accounts = parse_pubkeys(source_ix_accounts, "sourceIxAccounts")?;
        let is_v2 = is_v2_source_accounts(&source_accounts, mint);
        let is_token_2022 = *token_program_id == parse_pubkey(TOKEN_2022_PROGRAM_ID, "token 2022")?;
        if !is_token_2022 && !is_v2 && source_accounts.len() >= 16 {
            return build_source_replay_buy_ix(
                &source_accounts,
                &data,
                user,
                mint,
                token_program_id,
                cmd.exact_sol_in,
            );
        }
    }

    build_derived_buy_ix(cmd, user, mint, token_program_id, data)
}

struct ScaledBuyData {
    exact_sol_in: bool,
    amount: u64,
    max_sol_cost: u64,
}

fn scale_source_buy_data(data: &[u8], sol_in_lamports: u64, slippage_bps: u64) -> Result<ScaledBuyData> {
    let bps = slippage_bps.min(10_000);
    if starts_with(data, &BUY_DISCRIMINATOR) || starts_with(data, &BUY_V2_DISCRIMINATOR) {
        let source_tokens_out = read_u64_le(data, 8).context("source buy data missing token amount")?;
        let source_max_sol = read_u64_le(data, 16).context("source buy data missing max sol")?;
        if source_max_sol == 0 {
            bail!("source buy max sol is zero");
        }
        let amount = scale_u64(source_tokens_out, sol_in_lamports, source_max_sol);
        let max_sol_cost = sol_in_lamports.saturating_mul(10_000 + bps) / 10_000;
        return Ok(ScaledBuyData {
            exact_sol_in: false,
            amount,
            max_sol_cost,
        });
    }

    if starts_with(data, &BUY_EXACT_SOL_IN_DISCRIMINATOR)
        || starts_with(data, &BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR)
    {
        let source_spend = read_u64_le(data, 8).context("source exact-in data missing spend")?;
        let source_min_tokens_out = read_u64_le(data, 16).context("source exact-in data missing min out")?;
        if source_spend == 0 {
            bail!("source exact-in spend is zero");
        }
        let scaled_min = scale_u64(source_min_tokens_out, sol_in_lamports, source_spend);
        let amount = scaled_min.saturating_mul(10_000u64.saturating_sub(bps)) / 10_000;
        return Ok(ScaledBuyData {
            exact_sol_in: true,
            amount: amount.max(1),
            max_sol_cost: sol_in_lamports,
        });
    }

    bail!("source instruction is not a supported pump.fun buy layout")
}

fn source_data_for_scaled(source_data: &[u8], scaled: &ScaledBuyData) -> Vec<u8> {
    let mut data = source_data.to_vec();
    let discriminator = if starts_with(source_data, &BUY_V2_DISCRIMINATOR) {
        BUY_V2_DISCRIMINATOR
    } else if starts_with(source_data, &BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR) {
        BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR
    } else if scaled.exact_sol_in {
        BUY_EXACT_SOL_IN_DISCRIMINATOR
    } else {
        BUY_DISCRIMINATOR
    };
    data.resize(if starts_with(&discriminator, &BUY_DISCRIMINATOR) || starts_with(&discriminator, &BUY_EXACT_SOL_IN_DISCRIMINATOR) { 25 } else { 24 }, 0);
    data[0..8].copy_from_slice(&discriminator);
    if scaled.exact_sol_in {
        data[8..16].copy_from_slice(&scaled.max_sol_cost.to_le_bytes());
        data[16..24].copy_from_slice(&scaled.amount.to_le_bytes());
    } else {
        data[8..16].copy_from_slice(&scaled.amount.to_le_bytes());
        data[16..24].copy_from_slice(&scaled.max_sol_cost.to_le_bytes());
    }
    if data.len() == 25 {
        data[24] = 0;
    }
    data
}

fn encode_buy_args(cmd: &ExecutePumpfunBuyCommand, amount: u64, max_sol_cost: u64) -> Vec<u8> {
    let is_v2 = cmd
        .token_program_id
        .as_deref()
        .map(|value| value == TOKEN_2022_PROGRAM_ID)
        .unwrap_or(false);
    let discriminator = if is_v2 {
        if cmd.exact_sol_in {
            BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR
        } else {
            BUY_V2_DISCRIMINATOR
        }
    } else if cmd.exact_sol_in {
        BUY_EXACT_SOL_IN_DISCRIMINATOR
    } else {
        BUY_DISCRIMINATOR
    };
    let mut data = vec![0u8; if is_v2 { 24 } else { 25 }];
    data[0..8].copy_from_slice(&discriminator);
    if cmd.exact_sol_in {
        data[8..16].copy_from_slice(&max_sol_cost.to_le_bytes());
        data[16..24].copy_from_slice(&amount.to_le_bytes());
    } else {
        data[8..16].copy_from_slice(&amount.to_le_bytes());
        data[16..24].copy_from_slice(&max_sol_cost.to_le_bytes());
    }
    if !is_v2 {
        data[24] = 0;
    }
    data
}

fn build_source_replay_buy_ix(
    source_accounts: &[Pubkey],
    data: &[u8],
    user: &Pubkey,
    mint: &Pubkey,
    token_program_id: &Pubkey,
    exact_sol_in: bool,
) -> Result<Instruction> {
    let program_id = parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?;
    let associated_user = derive_ata(user, mint, token_program_id)?;
    let user_volume_accumulator = derive_user_volume_accumulator(user)?;
    let associated_quote_user = derive_ata(user, &parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?, &parse_pubkey(TOKEN_PROGRAM_ID, "token program")?)?;
    let associated_user_volume_accumulator =
        derive_ata(&user_volume_accumulator, &parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?, &parse_pubkey(TOKEN_PROGRAM_ID, "token program")?)?;

    let mut keys = Vec::new();
    if is_v2_source_accounts(source_accounts, mint) {
        for (i, source) in source_accounts.iter().take(PUMP_FUN_BUY_V2_ACCOUNT_COUNT).enumerate() {
            let actual = match i {
                13 => *user,
                14 => associated_user,
                15 => associated_quote_user,
                20 => user_volume_accumulator,
                21 => associated_user_volume_accumulator,
                _ => *source,
            };
            keys.push(meta(actual, i == 13, PUMP_FUN_BUY_V2_WRITABLE_FLAGS[i]));
        }
    } else {
        if source_accounts.len() < 16 {
            bail!("source replay requires at least 16 pump.fun accounts");
        }
        for (i, source) in source_accounts.iter().take(16).enumerate() {
            let actual = match i {
                5 => associated_user,
                6 => *user,
                13 => user_volume_accumulator,
                _ => *source,
            };
            let writable = PUMP_FUN_BUY_WRITABLE_FLAGS[i] || (exact_sol_in && i == 12);
            keys.push(meta(actual, i == 6, writable));
        }
        for source in source_accounts.iter().skip(16) {
            keys.push(AccountMeta::new(*source, false));
        }
    }

    Ok(Instruction {
        program_id,
        accounts: keys,
        data: data.to_vec(),
    })
}

fn build_derived_buy_ix(
    cmd: &ExecutePumpfunBuyCommand,
    user: &Pubkey,
    mint: &Pubkey,
    token_program_id: &Pubkey,
    data: Vec<u8>,
) -> Result<Instruction> {
    let is_v2 = *token_program_id == parse_pubkey(TOKEN_2022_PROGRAM_ID, "token 2022")?;
    if is_v2 {
        build_derived_v2_buy_ix(cmd, user, mint, token_program_id, data)
    } else {
        build_derived_legacy_buy_ix(cmd, user, mint, token_program_id, data)
    }
}

fn build_derived_legacy_buy_ix(
    cmd: &ExecutePumpfunBuyCommand,
    user: &Pubkey,
    mint: &Pubkey,
    token_program_id: &Pubkey,
    data: Vec<u8>,
) -> Result<Instruction> {
    let bonding_curve = parse_pubkey(cmd.bonding_curve.as_deref().context("direct buy missing bondingCurve")?, "bondingCurve")?;
    let creator = parse_pubkey(cmd.creator.as_deref().context("direct buy missing creator")?, "creator")?;
    let associated_bonding_curve = derive_ata(&bonding_curve, mint, token_program_id)?;
    let associated_user = derive_ata(user, mint, token_program_id)?;
    let creator_vault = derive_creator_vault(&creator)?;
    let keys = vec![
        meta(derive_global()?, false, false),
        meta(parse_pubkey(PUMP_FUN_FEE_RECIPIENT, "fee recipient")?, false, true),
        meta(*mint, false, false),
        meta(bonding_curve, false, true),
        meta(associated_bonding_curve, false, true),
        meta(associated_user, false, true),
        meta(*user, true, true),
        meta(solana_system_interface::program::ID, false, false),
        meta(*token_program_id, false, false),
        meta(creator_vault, false, true),
        meta(derive_event_authority()?, false, false),
        meta(parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?, false, false),
        meta(derive_global_volume_accumulator()?, false, cmd.exact_sol_in),
        meta(derive_user_volume_accumulator(user)?, false, true),
        meta(derive_fee_config()?, false, false),
        meta(parse_pubkey(PUMP_FUN_FEE_PROGRAM_ID, "fee program")?, false, false),
    ];
    Ok(Instruction {
        program_id: parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?,
        accounts: keys,
        data,
    })
}

fn build_derived_v2_buy_ix(
    cmd: &ExecutePumpfunBuyCommand,
    user: &Pubkey,
    mint: &Pubkey,
    token_program_id: &Pubkey,
    data: Vec<u8>,
) -> Result<Instruction> {
    let bonding_curve = parse_pubkey(cmd.bonding_curve.as_deref().context("direct buy missing bondingCurve")?, "bondingCurve")?;
    let creator = parse_pubkey(cmd.creator.as_deref().context("direct buy missing creator")?, "creator")?;
    let wsol = parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?;
    let token_program = parse_pubkey(TOKEN_PROGRAM_ID, "token program")?;
    let creator_vault = derive_creator_vault(&creator)?;
    let user_volume_accumulator = derive_user_volume_accumulator(user)?;
    let keys = vec![
        meta(derive_global()?, false, false),
        meta(*mint, false, false),
        meta(wsol, false, false),
        meta(*token_program_id, false, false),
        meta(token_program, false, false),
        meta(parse_pubkey(ASSOCIATED_TOKEN_PROGRAM_ID, "ata program")?, false, false),
        meta(parse_pubkey(PUMP_FUN_FEE_RECIPIENT, "fee recipient")?, false, true),
        meta(derive_ata(&parse_pubkey(PUMP_FUN_FEE_RECIPIENT, "fee recipient")?, &wsol, &token_program)?, false, true),
        meta(parse_pubkey(PUMP_FUN_BUYBACK_FEE_RECIPIENT, "buyback recipient")?, false, true),
        meta(derive_ata(&parse_pubkey(PUMP_FUN_BUYBACK_FEE_RECIPIENT, "buyback recipient")?, &wsol, &token_program)?, false, true),
        meta(bonding_curve, false, true),
        meta(derive_ata(&bonding_curve, mint, token_program_id)?, false, true),
        meta(derive_ata(&bonding_curve, &wsol, &token_program)?, false, true),
        meta(*user, true, true),
        meta(derive_ata(user, mint, token_program_id)?, false, true),
        meta(derive_ata(user, &wsol, &token_program)?, false, true),
        meta(creator_vault, false, true),
        meta(derive_ata(&creator_vault, &wsol, &token_program)?, false, true),
        meta(derive_sharing_config(mint)?, false, false),
        meta(derive_global_volume_accumulator()?, false, false),
        meta(user_volume_accumulator, false, true),
        meta(derive_ata(&user_volume_accumulator, &wsol, &token_program)?, false, true),
        meta(derive_fee_config()?, false, false),
        meta(parse_pubkey(PUMP_FUN_FEE_PROGRAM_ID, "fee program")?, false, false),
        meta(solana_system_interface::program::ID, false, false),
        meta(derive_event_authority()?, false, false),
        meta(parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?, false, false),
    ];
    Ok(Instruction {
        program_id: parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?,
        accounts: keys,
        data,
    })
}

fn create_ata_idempotent_ix(
    payer: &Pubkey,
    owner: &Pubkey,
    mint: &Pubkey,
    token_program_id: &Pubkey,
) -> Result<Instruction> {
    let ata = derive_ata(owner, mint, token_program_id)?;
    Ok(Instruction {
        program_id: parse_pubkey(ASSOCIATED_TOKEN_PROGRAM_ID, "ata program")?,
        accounts: vec![
            meta(*payer, true, true),
            meta(ata, false, true),
            meta(*owner, false, false),
            meta(*mint, false, false),
            meta(solana_system_interface::program::ID, false, false),
            meta(*token_program_id, false, false),
        ],
        data: vec![1],
    })
}

fn tip_instruction_sets(cmd: &ExecutePumpfunBuyCommand, user: &Pubkey) -> Result<Vec<Vec<Instruction>>> {
    let mut all = Vec::new();
    if cmd.use_jito_direct && cmd.jito_tip_lamports > 0 {
        all.push(system_instruction::transfer(
            user,
            &parse_pubkey(JITO_TIP_ACCOUNTS[0], "jito tip account")?,
            cmd.jito_tip_lamports,
        ));
    }
    if (!cmd.use_jito_direct || cmd.use_jito_fallback) && cmd.helius_tip_lamports > 0 {
        all.push(system_instruction::transfer(
            user,
            &parse_pubkey(HELIUS_TIP_ACCOUNTS[0], "helius tip account")?,
            cmd.helius_tip_lamports,
        ));
    }

    let mut sets = vec![all.clone()];
    if all.len() > 1 {
        sets.push(vec![all[0].clone()]);
        sets.push(vec![all[1].clone()]);
    }
    sets.push(Vec::new());
    Ok(sets)
}

async fn broadcast_signed_tx(cmd: &ExecutePumpfunBuyCommand, tx: &VersionedTransaction, signature: &str) -> Result<String> {
    let tx_base64 = BASE64_STANDARD.encode(bincode::serialize(tx).context("serialize tx for broadcast")?);
    let candidates = broadcast_candidates(cmd)?;
    if cmd.execution_multi_send_enabled {
        return race_broadcast(candidates, tx_base64, signature.to_string(), cmd.jito_auth_uuid.clone()).await;
    }

    let client = reqwest::Client::new();
    let mut errors = Vec::new();
    for candidate in candidates {
        match send_candidate(&client, &candidate, &tx_base64, signature, cmd.jito_auth_uuid.as_deref()).await {
            Ok(path) => return Ok(path),
            Err(err) => errors.push(format!("{}: {err}", candidate.name)),
        }
    }
    Err(anyhow!("all rust executor broadcast paths failed: {}", errors.join(" | ")))
}

fn broadcast_candidates(cmd: &ExecutePumpfunBuyCommand) -> Result<Vec<BroadcastCandidate>> {
    let mut candidates = Vec::new();
    if cmd.use_jito_direct {
        match cmd.jito_send_mode.as_str() {
            "transaction" | "tx" => {
                for url in &cmd.jito_transaction_urls {
                    candidates.push(BroadcastCandidate {
                        name: "jito-tx".to_string(),
                        url: url.clone(),
                        method: BroadcastMethod::JitoTransaction,
                    });
                }
            }
            "race" | "both" => {
                for url in &cmd.jito_transaction_urls {
                    candidates.push(BroadcastCandidate {
                        name: "jito-tx".to_string(),
                        url: url.clone(),
                        method: BroadcastMethod::JitoTransaction,
                    });
                }
                for url in &cmd.jito_bundle_urls {
                    candidates.push(BroadcastCandidate {
                        name: "jito-bundle".to_string(),
                        url: url.clone(),
                        method: BroadcastMethod::JitoBundle,
                    });
                }
            }
            _ => {
                for url in &cmd.jito_bundle_urls {
                    candidates.push(BroadcastCandidate {
                        name: "jito-bundle".to_string(),
                        url: url.clone(),
                        method: BroadcastMethod::JitoBundle,
                    });
                }
            }
        }
    }
    if !cmd.use_jito_direct || cmd.use_jito_fallback {
        candidates.push(BroadcastCandidate {
            name: "helius-sender".to_string(),
            url: cmd.sender_endpoint_url.clone(),
            method: BroadcastMethod::HeliusSender,
        });
    }
    if cmd.sender_rpc_fallback_enabled && !cmd.rpc_fallback_url.trim().is_empty() {
        candidates.push(BroadcastCandidate {
            name: "rpc-fallback".to_string(),
            url: cmd.rpc_fallback_url.clone(),
            method: BroadcastMethod::RpcFallback,
        });
    }
    if candidates.is_empty() {
        bail!("no rust executor broadcast candidates configured");
    }
    Ok(candidates)
}

async fn race_broadcast(
    candidates: Vec<BroadcastCandidate>,
    tx_base64: String,
    signature: String,
    jito_auth_uuid: Option<String>,
) -> Result<String> {
    let client = reqwest::Client::new();
    let mut futures = FuturesUnordered::new();
    for candidate in candidates {
        let client = client.clone();
        let tx_base64 = tx_base64.clone();
        let signature = signature.clone();
        let auth = jito_auth_uuid.clone();
        futures.push(async move {
            send_candidate(&client, &candidate, &tx_base64, &signature, auth.as_deref())
                .await
                .map_err(|err| format!("{}: {err}", candidate.name))
        });
    }
    let mut errors = Vec::new();
    while let Some(result) = futures.next().await {
        match result {
            Ok(path) => return Ok(path),
            Err(err) => errors.push(err),
        }
    }
    Err(anyhow!("all rust executor broadcast paths failed: {}", errors.join(" | ")))
}

async fn send_candidate(
    client: &reqwest::Client,
    candidate: &BroadcastCandidate,
    tx_base64: &str,
    signature: &str,
    jito_auth_uuid: Option<&str>,
) -> Result<String> {
    let (method, params) = match candidate.method {
        BroadcastMethod::JitoBundle => (
            "sendBundle",
            json!([[tx_base64], { "encoding": "base64" }]),
        ),
        BroadcastMethod::JitoTransaction | BroadcastMethod::HeliusSender | BroadcastMethod::RpcFallback => (
            "sendTransaction",
            json!([tx_base64, { "encoding": "base64", "skipPreflight": true, "maxRetries": 0 }]),
        ),
    };
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    });
    let mut request = client
        .post(&candidate.url)
        .timeout(Duration::from_millis(900))
        .json(&body);
    if let Some(auth) = jito_auth_uuid {
        if !auth.trim().is_empty() {
            request = request.header("x-jito-auth", auth);
        }
    }
    let response = request.send().await.context("http send failed")?;
    let json = response.json::<JsonRpcResponse>().await.context("json-rpc response parse failed")?;
    if let Some(error) = json.error {
        let code = error.code.map(|c| format!(" code {c}")).unwrap_or_default();
        bail!("{}{}", error.message.unwrap_or_else(|| "json-rpc error".to_string()), code);
    }
    match candidate.method {
        BroadcastMethod::JitoBundle => Ok(format!("{}:{}", candidate.name, signature)),
        _ => {
            let result = json
                .result
                .and_then(|v| v.as_str().map(str::to_string))
                .context("sendTransaction returned no signature")?;
            Ok(format!("{}:{}", candidate.name, result))
        }
    }
}

fn estimate_tokens_out(cmd: &ExecutePumpfunBuyCommand) -> Result<u64> {
    if cmd.mode == "source-replay" {
        let source_data = decode_source_data(cmd)?;
        return Ok(scale_source_buy_data(&source_data, cmd.sol_in_lamports, cmd.slippage_bps)?.amount);
    }
    Ok(cmd.tokens_out_raw.unwrap_or(1).max(1))
}

fn token_program_for_command(cmd: &ExecutePumpfunBuyCommand) -> Result<Pubkey> {
    if let Some(token_program) = &cmd.token_program_id {
        return parse_pubkey(token_program, "tokenProgramId");
    }
    if let Some(accounts) = &cmd.source_ix_accounts {
        let parsed = parse_pubkeys(accounts, "sourceIxAccounts")?;
        if is_v2_source_accounts(&parsed, &parse_pubkey(&cmd.mint, "mint")?) {
            return parsed.get(3).copied().context("source V2 accounts missing token program");
        }
        return parsed.get(8).copied().context("source legacy accounts missing token program");
    }
    parse_pubkey(TOKEN_PROGRAM_ID, "token program")
}

fn parse_source_accounts(cmd: &ExecutePumpfunBuyCommand) -> Result<Vec<Pubkey>> {
    parse_pubkeys(
        cmd.source_ix_accounts
            .as_ref()
            .context("source-replay requires sourceIxAccounts")?,
        "sourceIxAccounts",
    )
}

fn decode_source_data(cmd: &ExecutePumpfunBuyCommand) -> Result<Vec<u8>> {
    BASE64_STANDARD
        .decode(cmd.source_ix_data_base64.as_deref().context("source-replay requires sourceIxDataBase64")?)
        .context("sourceIxDataBase64 decode failed")
}

fn parse_pubkeys(values: &[String], label: &str) -> Result<Vec<Pubkey>> {
    values
        .iter()
        .map(|value| parse_pubkey(value, label))
        .collect::<Result<Vec<_>>>()
}

fn parse_pubkey(value: &str, label: &str) -> Result<Pubkey> {
    Pubkey::from_str(value).with_context(|| format!("invalid {label} pubkey: {value}"))
}

fn is_v2_source_accounts(source_accounts: &[Pubkey], mint: &Pubkey) -> bool {
    source_accounts.len() >= 27
        && source_accounts.get(1) == Some(mint)
        && source_accounts
            .get(26)
            .is_some_and(|v| *v == parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program").unwrap())
}

fn derive_ata(owner: &Pubkey, mint: &Pubkey, token_program_id: &Pubkey) -> Result<Pubkey> {
    let ata_program = parse_pubkey(ASSOCIATED_TOKEN_PROGRAM_ID, "ata program")?;
    Ok(Pubkey::find_program_address(
        &[owner.as_ref(), token_program_id.as_ref(), mint.as_ref()],
        &ata_program,
    )
    .0)
}

fn derive_global() -> Result<Pubkey> {
    pda(&[b"global"], PUMP_FUN_PROGRAM_ID)
}

fn derive_event_authority() -> Result<Pubkey> {
    pda(&[b"__event_authority"], PUMP_FUN_PROGRAM_ID)
}

fn derive_creator_vault(creator: &Pubkey) -> Result<Pubkey> {
    pda(&[b"creator-vault", creator.as_ref()], PUMP_FUN_PROGRAM_ID)
}

fn derive_global_volume_accumulator() -> Result<Pubkey> {
    pda(&[b"global_volume_accumulator"], PUMP_FUN_PROGRAM_ID)
}

fn derive_user_volume_accumulator(user: &Pubkey) -> Result<Pubkey> {
    pda(&[b"user_volume_accumulator", user.as_ref()], PUMP_FUN_PROGRAM_ID)
}

fn derive_fee_config() -> Result<Pubkey> {
    let pump = parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?;
    pda(&[b"fee_config", pump.as_ref()], PUMP_FUN_FEE_PROGRAM_ID)
}

fn derive_sharing_config(mint: &Pubkey) -> Result<Pubkey> {
    pda(&[b"sharing-config", mint.as_ref()], PUMP_FUN_FEE_PROGRAM_ID)
}

fn pda(seeds: &[&[u8]], program: &str) -> Result<Pubkey> {
    let program_id = parse_pubkey(program, "program")?;
    Ok(Pubkey::find_program_address(seeds, &program_id).0)
}

fn meta(pubkey: Pubkey, is_signer: bool, is_writable: bool) -> AccountMeta {
    if is_writable {
        AccountMeta::new(pubkey, is_signer)
    } else {
        AccountMeta::new_readonly(pubkey, is_signer)
    }
}

fn starts_with(data: &[u8], discriminator: &[u8; 8]) -> bool {
    data.len() >= discriminator.len() && &data[..8] == discriminator
}

fn read_u64_le(data: &[u8], offset: usize) -> Option<u64> {
    let bytes = data.get(offset..offset + 8)?;
    let mut arr = [0u8; 8];
    arr.copy_from_slice(bytes);
    Some(u64::from_le_bytes(arr))
}

fn scale_u64(value: u64, numerator: u64, denominator: u64) -> u64 {
    if denominator == 0 {
        return 1;
    }
    let scaled = (value as u128)
        .saturating_mul(numerator as u128)
        .saturating_div(denominator as u128);
    scaled.max(1).min(u64::MAX as u128) as u64
}
