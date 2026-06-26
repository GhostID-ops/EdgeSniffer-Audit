use std::{
    collections::HashSet,
    env,
    fs::{self, OpenOptions},
    io::{ErrorKind, Write},
    path::PathBuf,
    str::FromStr,
    sync::{Arc, Mutex, OnceLock},
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
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const PUMP_FUN_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMPSWAP_PROGRAM_ID: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const RAYDIUM_CPMM_PROGRAM_ID: &str = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C";
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
const PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR: [u8; 8] = [198, 46, 21, 82, 180, 217, 232, 112];
const PUMPSWAP_SELL_DISCRIMINATOR: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const PUMP_FUN_SELL_DISCRIMINATOR: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const PUMP_FUN_SELL_V2_DISCRIMINATOR: [u8; 8] = [93, 246, 130, 60, 231, 233, 64, 178];
const SYNC_NATIVE_DISCRIMINATOR: [u8; 1] = [17];
const CLOSE_ACCOUNT_DISCRIMINATOR: [u8; 1] = [9];

const PUMP_FUN_BUY_WRITABLE_FLAGS: [bool; 16] = [
    false, true, false, true, true, true, true, false, false, true, false, false, false, true,
    false, false,
];
const PUMP_FUN_BUY_V2_WRITABLE_FLAGS: [bool; 27] = [
    false, false, false, false, false, false, true, true, true, true, true, true, true, true, true,
    true, true, true, false, false, true, true, false, false, false, false, false,
];
const PUMP_FUN_BUY_V2_ACCOUNT_COUNT: usize = 27;
const PUMPSWAP_USER_INDEX: usize = 1;
const PUMPSWAP_BASE_MINT_INDEX: usize = 3;
const PUMPSWAP_QUOTE_MINT_INDEX: usize = 4;
const PUMPSWAP_USER_BASE_ATA_INDEX: usize = 5;
const PUMPSWAP_USER_QUOTE_ATA_INDEX: usize = 6;
const PUMPSWAP_BASE_TOKEN_PROGRAM_INDEX: usize = 11;
const PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX: usize = 20;
const PUMPSWAP_SELL_CASHBACK_ATA_INDEX: usize = 21;
const PUMPSWAP_SELL_CASHBACK_USER_VOLUME_INDEX: usize = 22;
const PUMPSWAP_MIN_BUY_ACCOUNT_COUNT: usize = 23;
const PUMPSWAP_MIN_SELL_ACCOUNT_COUNT: usize = 21;

fn pumpswap_canonical_buy_writable(index: usize) -> bool {
    matches!(index, 0 | 1 | 5 | 6 | 7 | 8 | 10 | 17 | 20 | 23)
}

fn pumpswap_canonical_sell_writable(index: usize, has_cashback_sell_tail: bool) -> bool {
    matches!(index, 0 | 1 | 5 | 6 | 7 | 8 | 10 | 17)
        || (has_cashback_sell_tail
            && matches!(index, PUMPSWAP_SELL_CASHBACK_ATA_INDEX | PUMPSWAP_SELL_CASHBACK_USER_VOLUME_INDEX))
}

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

static HTTP_CLIENT: OnceLock<reqwest::Client> = OnceLock::new();

pub type SharedBoughtMints = Arc<Mutex<HashSet<String>>>;

fn http_client() -> reqwest::Client {
    HTTP_CLIENT.get_or_init(reqwest::Client::new).clone()
}

#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
enum ExecutorCommand {
    ExecutePumpfunBuy(ExecutePumpfunBuyCommand),
    ExecutePumpfunSell(ExecutePumpfunSellCommand),
    ExecutePumpSwapBuy(ExecutePumpSwapBuyCommand),
    ExecutePumpSwapSell(ExecutePumpSwapSellCommand),
    ExecuteRaydiumCpmmSwap(ExecuteRaydiumCpmmSwapCommand),
    ReleaseBoughtMint(ReleaseBoughtMintCommand),
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutePumpfunBuyCommand {
    pub id: String,
    pub mode: String,
    pub route: String,
    pub mint: String,
    /// Source (tracked-wallet) tx being mirrored. Used for the always-on
    /// per-source-signature buy-once gate so the executor bridge and the
    /// ShredStream hot sniper can never both land a buy for the same source tx.
    /// Optional/absent for manual or legacy callers (gate is then skipped).
    #[serde(default)]
    pub source_signature: Option<String>,
    #[serde(default)]
    pub quote_mint: Option<String>,
    pub sol_in_lamports: u64,
    pub tokens_out_raw: Option<u64>,
    pub slippage_bps: u64,
    pub exact_sol_in: bool,
    pub source_ix_accounts: Option<Vec<String>>,
    pub source_ix_data_base64: Option<String>,
    pub bonding_curve: Option<String>,
    pub creator: Option<String>,
    pub token_program_id: Option<String>,
    pub blockhash: String,
    pub compute_unit_limit: u32,
    pub priority_fee_micro_lamports: u64,
    pub jito_tip_lamports: u64,
    pub helius_tip_lamports: u64,
    pub use_jito_direct: bool,
    pub use_jito_fallback: bool,
    pub sender_rpc_fallback_enabled: bool,
    pub execution_multi_send_enabled: bool,
    pub jito_send_mode: String,
    pub jito_transaction_urls: Vec<String>,
    pub jito_bundle_urls: Vec<String>,
    pub sender_endpoint_url: String,
    pub rpc_fallback_url: String,
    pub jito_auth_uuid: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutePumpSwapBuyCommand {
    pub id: String,
    pub route: String,
    pub mint: String,
    pub source_signature: String,
    pub buy_lock_owner: Option<String>,
    pub quote_mint: String,
    pub quote_amount_in_raw: u64,
    pub expected_base_out_raw: u64,
    pub min_base_out_raw: u64,
    pub buy_instruction: String,
    pub source_pump_swap_accounts: Vec<SourcePumpSwapAccountMeta>,
    pub base_token_program_id: String,
    pub blockhash: String,
    pub compute_unit_limit: u32,
    pub priority_fee_micro_lamports: u64,
    pub jito_tip_lamports: u64,
    pub helius_tip_lamports: u64,
    pub use_jito_direct: bool,
    pub use_jito_fallback: bool,
    pub sender_rpc_fallback_enabled: bool,
    pub execution_multi_send_enabled: bool,
    pub jito_send_mode: String,
    pub jito_transaction_urls: Vec<String>,
    pub jito_bundle_urls: Vec<String>,
    pub sender_endpoint_url: String,
    pub rpc_fallback_url: String,
    pub jito_auth_uuid: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutePumpSwapSellCommand {
    pub id: String,
    pub route: String,
    pub mint: String,
    pub tokens_in_raw: u64,
    pub expected_quote_out_raw: u64,
    pub min_quote_out_raw: u64,
    pub source_pump_swap_accounts: Vec<SourcePumpSwapAccountMeta>,
    pub base_token_program_id: String,
    pub blockhash: String,
    pub compute_unit_limit: u32,
    pub priority_fee_micro_lamports: u64,
    pub jito_tip_lamports: u64,
    pub helius_tip_lamports: u64,
    pub use_jito_direct: bool,
    pub use_jito_fallback: bool,
    pub sender_rpc_fallback_enabled: bool,
    pub execution_multi_send_enabled: bool,
    pub jito_send_mode: String,
    pub jito_transaction_urls: Vec<String>,
    pub jito_bundle_urls: Vec<String>,
    pub sender_endpoint_url: String,
    pub rpc_fallback_url: String,
    pub jito_auth_uuid: Option<String>,
}


#[derive(Debug, Deserialize)]
#[allow(dead_code)]
#[serde(rename_all = "camelCase")]
pub struct ExecuteRaydiumCpmmSwapCommand {
    pub id: String,
    pub route: String,
    pub side: String,
    pub mint: String,
    pub source_signature: String,
    pub input_mint: String,
    pub output_mint: String,
    pub amount_in_raw: u64,
    pub expected_out_raw: u64,
    pub min_out_raw: u64,
    pub source_raydium_cpmm_accounts: Vec<SourcePumpSwapAccountMeta>,
    pub blockhash: String,
    pub compute_unit_limit: u32,
    pub priority_fee_micro_lamports: u64,
    pub jito_tip_lamports: u64,
    pub helius_tip_lamports: u64,
    pub use_jito_direct: bool,
    pub use_jito_fallback: bool,
    pub sender_rpc_fallback_enabled: bool,
    pub execution_multi_send_enabled: bool,
    pub jito_send_mode: String,
    pub jito_transaction_urls: Vec<String>,
    pub jito_bundle_urls: Vec<String>,
    pub sender_endpoint_url: String,
    pub rpc_fallback_url: String,
    pub jito_auth_uuid: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ExecutePumpfunSellCommand {
    pub id: String,
    pub route: String,
    pub mint: String,
    pub tokens_in_raw: u64,
    pub expected_sol_out_lamports: u64,
    pub min_sol_out_lamports: u64,
    pub sell_v2: bool,
    pub accounts: Vec<SourcePumpSwapAccountMeta>,
    pub blockhash: String,
    pub compute_unit_limit: u32,
    pub priority_fee_micro_lamports: u64,
    pub jito_tip_lamports: u64,
    pub helius_tip_lamports: u64,
    pub use_jito_direct: bool,
    pub use_jito_fallback: bool,
    pub sender_rpc_fallback_enabled: bool,
    pub execution_multi_send_enabled: bool,
    pub jito_send_mode: String,
    pub jito_transaction_urls: Vec<String>,
    pub jito_bundle_urls: Vec<String>,
    pub sender_endpoint_url: String,
    pub rpc_fallback_url: String,
    pub jito_auth_uuid: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SourcePumpSwapAccountMeta {
    pub pubkey: String,
    pub is_signer: bool,
    pub is_writable: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReleaseBoughtMintCommand {
    pub id: String,
    pub mint: String,
    pub reason: Option<String>,
}

#[derive(Clone)]
struct BroadcastConfig {
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
pub struct ExecutorResponse {
    pub kind: &'static str,
    pub id: String,
    pub ok: bool,
    pub signature: Option<String>,
    pub route: Option<String>,
    pub sender_path: Option<String>,
    pub sol_in_lamports: Option<u64>,
    pub tokens_out_raw: Option<u64>,
    pub elapsed_ms: u128,
    pub build_ms: Option<u128>,
    pub broadcast_ms: Option<u128>,
    pub tx_bytes: Option<usize>,
    pub error: Option<String>,
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

impl BroadcastConfig {
    fn from_pumpfun(cmd: &ExecutePumpfunBuyCommand) -> Self {
        Self {
            use_jito_direct: cmd.use_jito_direct,
            use_jito_fallback: cmd.use_jito_fallback,
            sender_rpc_fallback_enabled: cmd.sender_rpc_fallback_enabled,
            execution_multi_send_enabled: cmd.execution_multi_send_enabled,
            jito_send_mode: cmd.jito_send_mode.clone(),
            jito_transaction_urls: cmd.jito_transaction_urls.clone(),
            jito_bundle_urls: cmd.jito_bundle_urls.clone(),
            sender_endpoint_url: cmd.sender_endpoint_url.clone(),
            rpc_fallback_url: cmd.rpc_fallback_url.clone(),
            jito_auth_uuid: cmd.jito_auth_uuid.clone(),
        }
    }

    fn from_pumpfun_sell(cmd: &ExecutePumpfunSellCommand) -> Self {
        Self {
            use_jito_direct: cmd.use_jito_direct,
            use_jito_fallback: cmd.use_jito_fallback,
            sender_rpc_fallback_enabled: cmd.sender_rpc_fallback_enabled,
            execution_multi_send_enabled: cmd.execution_multi_send_enabled,
            jito_send_mode: cmd.jito_send_mode.clone(),
            jito_transaction_urls: cmd.jito_transaction_urls.clone(),
            jito_bundle_urls: cmd.jito_bundle_urls.clone(),
            sender_endpoint_url: cmd.sender_endpoint_url.clone(),
            rpc_fallback_url: cmd.rpc_fallback_url.clone(),
            jito_auth_uuid: cmd.jito_auth_uuid.clone(),
        }
    }

    fn from_pumpswap(cmd: &ExecutePumpSwapBuyCommand) -> Self {
        Self {
            use_jito_direct: cmd.use_jito_direct,
            use_jito_fallback: cmd.use_jito_fallback,
            sender_rpc_fallback_enabled: cmd.sender_rpc_fallback_enabled,
            execution_multi_send_enabled: cmd.execution_multi_send_enabled,
            jito_send_mode: cmd.jito_send_mode.clone(),
            jito_transaction_urls: cmd.jito_transaction_urls.clone(),
            jito_bundle_urls: cmd.jito_bundle_urls.clone(),
            sender_endpoint_url: cmd.sender_endpoint_url.clone(),
            rpc_fallback_url: cmd.rpc_fallback_url.clone(),
            jito_auth_uuid: cmd.jito_auth_uuid.clone(),
        }
    }


    fn from_raydium_cpmm(cmd: &ExecuteRaydiumCpmmSwapCommand) -> Self {
        Self {
            use_jito_direct: cmd.use_jito_direct,
            use_jito_fallback: cmd.use_jito_fallback,
            sender_rpc_fallback_enabled: cmd.sender_rpc_fallback_enabled,
            execution_multi_send_enabled: cmd.execution_multi_send_enabled,
            jito_send_mode: cmd.jito_send_mode.clone(),
            jito_transaction_urls: cmd.jito_transaction_urls.clone(),
            jito_bundle_urls: cmd.jito_bundle_urls.clone(),
            sender_endpoint_url: cmd.sender_endpoint_url.clone(),
            rpc_fallback_url: cmd.rpc_fallback_url.clone(),
            jito_auth_uuid: cmd.jito_auth_uuid.clone(),
        }
    }
    fn from_pumpswap_sell(cmd: &ExecutePumpSwapSellCommand) -> Self {
        Self {
            use_jito_direct: cmd.use_jito_direct,
            use_jito_fallback: cmd.use_jito_fallback,
            sender_rpc_fallback_enabled: cmd.sender_rpc_fallback_enabled,
            execution_multi_send_enabled: cmd.execution_multi_send_enabled,
            jito_send_mode: cmd.jito_send_mode.clone(),
            jito_transaction_urls: cmd.jito_transaction_urls.clone(),
            jito_bundle_urls: cmd.jito_bundle_urls.clone(),
            sender_endpoint_url: cmd.sender_endpoint_url.clone(),
            rpc_fallback_url: cmd.rpc_fallback_url.clone(),
            jito_auth_uuid: cmd.jito_auth_uuid.clone(),
        }
    }
}

pub fn start_executor_stdin(
    shared_bought_mints: Option<SharedBoughtMints>,
    shared_bought_signatures: Option<SharedBoughtMints>,
) {
    tokio::spawn(async move {
        let stdin = BufReader::new(io::stdin());
        let mut lines = stdin.lines();
        if shared_bought_signatures.is_some() {
            eprintln!(
                "rust-sidecar: Rust executor command channel ready (per-source-signature buy-once gate enabled)"
            );
        } else if shared_bought_mints.is_some() {
            eprintln!(
                "rust-sidecar: Rust executor command channel ready (shared buy-once gate enabled)"
            );
        } else {
            eprintln!("rust-sidecar: Rust executor command channel ready");
        }
        loop {
            match lines.next_line().await {
                Ok(Some(line)) => {
                    if line.trim().is_empty() {
                        continue;
                    }
                    let shared_bought_mints = shared_bought_mints.clone();
                    let shared_bought_signatures = shared_bought_signatures.clone();
                    tokio::spawn(async move {
                        let response =
                            handle_line(&line, shared_bought_mints, shared_bought_signatures).await;
                        match serde_json::to_string(&response) {
                            Ok(json) => println!("{json}"),
                            Err(err) => eprintln!(
                                "rust-sidecar: executor response serialization failed: {err}"
                            ),
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

async fn handle_line(
    line: &str,
    shared_bought_mints: Option<SharedBoughtMints>,
    shared_bought_signatures: Option<SharedBoughtMints>,
) -> ExecutorResponse {
    let started = Instant::now();
    let id = extract_id(line).unwrap_or_else(|| "unknown".to_string());
    match serde_json::from_str::<ExecutorCommand>(line) {
        Ok(ExecutorCommand::ExecutePumpfunBuy(cmd)) => {
            let id = cmd.id.clone();
            let mint = cmd.mint.clone();
            let source_signature = cmd.source_signature.clone();
            let reserved = match reserve_executor_mint(shared_bought_mints.as_ref(), &mint) {
                Ok(reserved) => reserved,
                Err(error) => {
                    return ExecutorResponse {
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
                        error: Some(error),
                    };
                }
            };
            let signature_reserved = match reserve_executor_signature(
                shared_bought_signatures.as_ref(),
                source_signature.as_deref(),
            ) {
                Ok(reserved) => reserved,
                Err(error) => {
                    if reserved {
                        release_executor_mint(shared_bought_mints.as_ref(), &mint);
                    }
                    return ExecutorResponse {
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
                        error: Some(error),
                    };
                }
            };
            match execute_pumpfun_buy(cmd, started).await {
                Ok(mut response) => {
                    response.id = id;
                    response
                }
                Err(err) => {
                    if reserved {
                        release_executor_mint(shared_bought_mints.as_ref(), &mint);
                    }
                    if signature_reserved {
                        release_executor_signature(
                            shared_bought_signatures.as_ref(),
                            source_signature.as_deref(),
                        );
                    }
                    ExecutorResponse {
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
                    }
                }
            }
        }
        Ok(ExecutorCommand::ExecutePumpfunSell(cmd)) => {
            let id = cmd.id.clone();
            match execute_pumpfun_sell(cmd, started).await {
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
        Ok(ExecutorCommand::ExecutePumpSwapBuy(cmd)) => {
            let id = cmd.id.clone();
            let mint = cmd.mint.clone();
            let source_signature = cmd.source_signature.clone();
            let reserved = match reserve_executor_mint(shared_bought_mints.as_ref(), &mint) {
                Ok(reserved) => reserved,
                Err(error) => {
                    return ExecutorResponse {
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
                        error: Some(error),
                    };
                }
            };
            let signature_reserved = match reserve_executor_signature(
                shared_bought_signatures.as_ref(),
                Some(source_signature.as_str()),
            ) {
                Ok(reserved) => reserved,
                Err(error) => {
                    if reserved {
                        release_executor_mint(shared_bought_mints.as_ref(), &mint);
                    }
                    return ExecutorResponse {
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
                        error: Some(error),
                    };
                }
            };
            let persistent_lock = match acquire_persistent_buy_lock(
                &mint,
                &cmd.source_signature,
                cmd.buy_lock_owner.as_deref(),
            ) {
                Ok(lock) => lock,
                Err(err) => {
                    if reserved {
                        release_executor_mint(shared_bought_mints.as_ref(), &mint);
                    }
                    if signature_reserved {
                        release_executor_signature(
                            shared_bought_signatures.as_ref(),
                            Some(source_signature.as_str()),
                        );
                    }
                    return ExecutorResponse {
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
                    };
                }
            };
            match execute_pumpswap_buy(cmd, started).await {
                Ok(mut response) => {
                    response.id = id;
                    response
                }
                Err(err) => {
                    if reserved {
                        release_executor_mint(shared_bought_mints.as_ref(), &mint);
                    }
                    if signature_reserved {
                        release_executor_signature(
                            shared_bought_signatures.as_ref(),
                            Some(source_signature.as_str()),
                        );
                    }
                    release_persistent_buy_lock(persistent_lock);
                    ExecutorResponse {
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
                    }
                }
            }
        }
        Ok(ExecutorCommand::ExecuteRaydiumCpmmSwap(cmd)) => {
            let id = cmd.id.clone();
            match execute_raydium_cpmm_swap(cmd, started).await {
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
        Ok(ExecutorCommand::ExecutePumpSwapSell(cmd)) => {
            let id = cmd.id.clone();
            match execute_pumpswap_sell(cmd, started).await {
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
        Ok(ExecutorCommand::ReleaseBoughtMint(cmd)) => {
            release_executor_mint(shared_bought_mints.as_ref(), &cmd.mint);
            let route = cmd
                .reason
                .as_ref()
                .map(|_| "release-bought-mint".to_string());
            ExecutorResponse {
                kind: "execResult",
                id: cmd.id,
                ok: true,
                signature: None,
                route,
                sender_path: None,
                sol_in_lamports: None,
                tokens_out_raw: None,
                elapsed_ms: started.elapsed().as_millis(),
                build_ms: None,
                broadcast_ms: None,
                tx_bytes: None,
                error: None,
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

fn reserve_executor_mint(
    shared_bought_mints: Option<&SharedBoughtMints>,
    mint: &str,
) -> Result<bool, String> {
    let Some(shared_bought_mints) = shared_bought_mints else {
        return Ok(false);
    };
    let mut bought = shared_bought_mints
        .lock()
        .map_err(|_| "buy-once state lock poisoned".to_string())?;
    if bought.contains(mint) {
        return Err("mint already bought/reserved by Rust this session".to_string());
    }
    bought.insert(mint.to_string());
    Ok(true)
}

fn release_executor_mint(shared_bought_mints: Option<&SharedBoughtMints>, mint: &str) {
    if let Some(shared_bought_mints) = shared_bought_mints {
        if let Ok(mut bought) = shared_bought_mints.lock() {
            bought.remove(mint);
        }
    }
}

/// Reserve a source signature in the shared set (same set the ShredStream hot
/// sniper uses). Always-on, independent of BUY_ONCE_PER_TOKEN: each source tx
/// can only be mirrored once across both buy paths. Returns Ok(true) if newly
/// reserved here, Ok(false) if there is nothing to gate on (no shared set or no
/// signature), Err if it was already mirrored this session.
fn reserve_executor_signature(
    shared_bought_signatures: Option<&SharedBoughtMints>,
    source_signature: Option<&str>,
) -> Result<bool, String> {
    let (Some(shared), Some(signature)) = (shared_bought_signatures, source_signature) else {
        return Ok(false);
    };
    if signature.is_empty() {
        return Ok(false);
    }
    let mut seen = shared
        .lock()
        .map_err(|_| "buy-once signature lock poisoned".to_string())?;
    if seen.contains(signature) {
        return Err("source signature already mirrored by Rust this session".to_string());
    }
    seen.insert(signature.to_string());
    Ok(true)
}

fn release_executor_signature(
    shared_bought_signatures: Option<&SharedBoughtMints>,
    source_signature: Option<&str>,
) {
    if let (Some(shared), Some(signature)) = (shared_bought_signatures, source_signature) {
        if let Ok(mut seen) = shared.lock() {
            seen.remove(signature);
        }
    }
}

enum PersistentBuyLock {
    Owned(PathBuf),
    Borrowed,
    Disabled,
}

fn buy_once_enabled() -> bool {
    env_flag("BUY_ONCE_PER_TOKEN", true)
}

fn env_flag(name: &str, default_value: bool) -> bool {
    match env::var(name) {
        Ok(value) => {
            let normalized = value.trim().to_ascii_lowercase();
            matches!(normalized.as_str(), "true" | "1" | "yes" | "on")
        }
        Err(_) => default_value,
    }
}

fn persistent_buy_lock_dir() -> PathBuf {
    env::var("BUY_LOCK_DIR")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .map(PathBuf::from)
        .unwrap_or_else(|| env::temp_dir().join("finalbot-buy-locks"))
}

fn sanitize_lock_component(value: &str) -> String {
    value
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || *ch == '-' || *ch == '_')
        .collect()
}

fn existing_lock_value(path: &PathBuf, key: &str) -> Option<String> {
    let text = fs::read_to_string(path).ok()?;
    let value = serde_json::from_str::<Value>(&text).ok()?;
    value.get(key).and_then(Value::as_str).map(str::to_string)
}

fn buy_lock_stale_ms() -> u128 {
    env::var("BUY_LOCK_STALE_MS")
        .ok()
        .and_then(|value| value.trim().parse::<u128>().ok())
        .filter(|value| *value >= 10_000)
        .unwrap_or(120_000)
}

fn release_stale_existing_lock(path: &PathBuf, mint: &str) -> bool {
    let threshold_ms = buy_lock_stale_ms();
    let now_ms = now_unix_ms();
    let text = match fs::read_to_string(path) {
        Ok(text) => text,
        Err(err) if err.kind() == ErrorKind::NotFound => return true,
        Err(_) => return false,
    };
    let created_at_ms = serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|value| value.get("createdAtMs").and_then(Value::as_u64).map(u128::from));
    let age_ms = created_at_ms
        .map(|created| now_ms.saturating_sub(created))
        .unwrap_or(u128::MAX);
    if age_ms < threshold_ms {
        return false;
    }
    match fs::remove_file(path) {
        Ok(_) => {
            eprintln!(
                "rust-sidecar: cleared stale persistent buy lock for {}... (age {}ms >= {}ms)",
                &mint.chars().take(8).collect::<String>(),
                age_ms,
                threshold_ms
            );
            true
        }
        Err(err) if err.kind() == ErrorKind::NotFound => true,
        Err(_) => false,
    }
}

fn acquire_persistent_buy_lock(
    mint: &str,
    source_signature: &str,
    buy_lock_owner: Option<&str>,
) -> Result<PersistentBuyLock> {
    if !buy_once_enabled() {
        return Ok(PersistentBuyLock::Disabled);
    }

    let dir = persistent_buy_lock_dir();
    fs::create_dir_all(&dir)
        .with_context(|| format!("failed to create buy lock dir {}", dir.display()))?;
    let lock_name = sanitize_lock_component(mint);
    if lock_name.is_empty() {
        bail!("cannot create buy lock for empty mint");
    }
    let path = dir.join(format!("{lock_name}.lock"));

    match OpenOptions::new().write(true).create_new(true).open(&path) {
        Ok(mut file) => {
            let payload = json!({
                "mint": mint,
                "sourceSignature": source_signature,
                "pid": std::process::id(),
                "createdAtMs": now_unix_ms(),
                "owner": buy_lock_owner.unwrap_or("rust-executor")
            });
            writeln!(file, "{payload}")
                .with_context(|| format!("failed to write buy lock {}", path.display()))?;
            Ok(PersistentBuyLock::Owned(path))
        }
        Err(err) if err.kind() == ErrorKind::AlreadyExists => {
            if buy_lock_owner.is_some()
                && existing_lock_value(&path, "owner").as_deref() == buy_lock_owner
            {
                return Ok(PersistentBuyLock::Borrowed);
            }
            if release_stale_existing_lock(&path, mint) {
                return acquire_persistent_buy_lock(mint, source_signature, buy_lock_owner);
            }
            bail!("persistent buy lock exists for {mint}; duplicate Rust broadcast blocked")
        }
        Err(err) => {
            Err(err).with_context(|| format!("failed to create buy lock {}", path.display()))
        }
    }
}

fn release_persistent_buy_lock(lock: PersistentBuyLock) {
    if let PersistentBuyLock::Owned(path) = lock {
        let _ = fs::remove_file(path);
    }
}

fn now_unix_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or(0)
}

fn extract_id(line: &str) -> Option<String> {
    serde_json::from_str::<Value>(line)
        .ok()
        .and_then(|v| v.get("id").and_then(Value::as_str).map(str::to_string))
}

async fn execute_pumpfun_buy(
    cmd: ExecutePumpfunBuyCommand,
    started: Instant,
) -> Result<ExecutorResponse> {
    let built = build_pumpfun_buy_tx(&cmd)?;
    let broadcast_started = Instant::now();
    let sender_path = broadcast_signed_tx(
        &BroadcastConfig::from_pumpfun(&cmd),
        &built.tx,
        &built.signature,
    )
    .await?;
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

async fn execute_pumpswap_buy(
    cmd: ExecutePumpSwapBuyCommand,
    started: Instant,
) -> Result<ExecutorResponse> {
    let built = build_pumpswap_buy_tx(&cmd)?;
    let broadcast_started = Instant::now();
    let sender_path = broadcast_signed_tx(
        &BroadcastConfig::from_pumpswap(&cmd),
        &built.tx,
        &built.signature,
    )
    .await?;
    Ok(ExecutorResponse {
        kind: "execResult",
        id: cmd.id,
        ok: true,
        signature: Some(built.signature),
        route: Some(cmd.route),
        sender_path: Some(sender_path),
        sol_in_lamports: Some(cmd.quote_amount_in_raw),
        tokens_out_raw: Some(cmd.expected_base_out_raw.max(1)),
        elapsed_ms: started.elapsed().as_millis(),
        build_ms: Some(built.build_ms),
        broadcast_ms: Some(broadcast_started.elapsed().as_millis()),
        tx_bytes: Some(built.bytes),
        error: None,
    })
}


async fn execute_raydium_cpmm_swap(
    cmd: ExecuteRaydiumCpmmSwapCommand,
    started: Instant,
) -> Result<ExecutorResponse> {
    let built = build_raydium_cpmm_swap_tx(&cmd)?;
    let broadcast_started = Instant::now();
    let sender_path = broadcast_signed_tx(
        &BroadcastConfig::from_raydium_cpmm(&cmd),
        &built.tx,
        &built.signature,
    )
    .await?;
    Ok(ExecutorResponse {
        kind: "execResult",
        id: cmd.id,
        ok: true,
        signature: Some(built.signature),
        route: Some(cmd.route),
        sender_path: Some(sender_path),
        sol_in_lamports: Some(if cmd.input_mint == WRAPPED_SOL_MINT { cmd.amount_in_raw } else { 0 }),
        tokens_out_raw: Some(cmd.expected_out_raw.max(1)),
        elapsed_ms: started.elapsed().as_millis(),
        build_ms: Some(built.build_ms),
        broadcast_ms: Some(broadcast_started.elapsed().as_millis()),
        tx_bytes: Some(built.bytes),
        error: None,
    })
}

fn build_raydium_cpmm_swap_tx(cmd: &ExecuteRaydiumCpmmSwapCommand) -> Result<BuiltTx> {
    let started = Instant::now();
    let private_key = env::var("PRIVATE_KEY_BASE58").context("PRIVATE_KEY_BASE58 is not set")?;
    let keypair_bytes = bs58::decode(private_key.trim())
        .into_vec()
        .context("PRIVATE_KEY_BASE58 is not valid base58")?;
    let keypair = Keypair::from_bytes(&keypair_bytes)
        .context("PRIVATE_KEY_BASE58 is not a Solana keypair")?;
    let user = keypair.pubkey();
    let input_mint = parse_supported_swap_mint(&cmd.input_mint, "inputMint")?;
    let output_mint = parse_supported_swap_mint(&cmd.output_mint, "outputMint")?;
    let blockhash = Hash::from_str(&cmd.blockhash).context("invalid blockhash")?;

    if cmd.source_raydium_cpmm_accounts.len() < 13 {
        bail!("Raydium CPMM source accounts unavailable");
    }
    let source = parse_source_meta_accounts(&cmd.source_raydium_cpmm_accounts, "sourceRaydiumCpmmAccounts")?;
    let input_token_program = source[8].pubkey;
    let output_token_program = source[9].pubkey;
    if source[10].pubkey != input_mint {
        bail!("Raydium CPMM source input mint does not match mirrored input mint");
    }
    if source[11].pubkey != output_mint {
        bail!("Raydium CPMM source output mint does not match mirrored output mint");
    }
    let user_input_ata = derive_ata(&user, &input_mint, &input_token_program)?;
    let user_output_ata = derive_ata(&user, &output_mint, &output_token_program)?;

    let mut instructions = Vec::with_capacity(9);
    if cmd.compute_unit_limit > 0 {
        instructions.push(ComputeBudgetInstruction::set_compute_unit_limit(
            cmd.compute_unit_limit.min(1_400_000),
        ));
    }
    instructions.push(ComputeBudgetInstruction::set_compute_unit_price(
        cmd.priority_fee_micro_lamports,
    ));
    instructions.push(create_ata_idempotent_ix(&user, &user, &input_mint, &input_token_program)?);
    instructions.push(create_ata_idempotent_ix(&user, &user, &output_mint, &output_token_program)?);
    if cmd.input_mint == WRAPPED_SOL_MINT {
        instructions.push(system_instruction::transfer(&user, &user_input_ata, cmd.amount_in_raw));
        instructions.push(sync_native_ix(&user_input_ata)?);
    }
    instructions.push(build_raydium_cpmm_swap_ix(cmd, &source, &user, &user_input_ata, &user_output_ata)?);
    if cmd.input_mint == WRAPPED_SOL_MINT {
        instructions.push(close_token_account_ix(&user_input_ata, &user)?);
    } else if cmd.output_mint == WRAPPED_SOL_MINT {
        instructions.push(close_token_account_ix(&user_output_ata, &user)?);
    }

    let tip_sets = tip_instruction_sets(
        &user,
        cmd.jito_tip_lamports,
        cmd.helius_tip_lamports,
        cmd.use_jito_direct,
        cmd.use_jito_fallback,
    )?;
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
                return Ok(BuiltTx {
                    tx,
                    signature,
                    tokens_out_raw: cmd.expected_out_raw.max(1),
                    bytes,
                    build_ms: started.elapsed().as_millis(),
                });
            }
            Ok((_tx, bytes)) => {
                last_error = Some(anyhow!(
                    "transaction too large: {bytes}B > {PACKET_DATA_SIZE}B"
                ));
            }
            Err(err) => last_error = Some(err),
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow!("no tip set could build a Raydium CPMM transaction")))
}

fn build_raydium_cpmm_swap_ix(
    cmd: &ExecuteRaydiumCpmmSwapCommand,
    source: &[AccountMeta],
    user: &Pubkey,
    user_input_ata: &Pubkey,
    user_output_ata: &Pubkey,
) -> Result<Instruction> {
    let mut accounts = source.to_vec();
    accounts[0] = meta(*user, true, true);
    accounts[4] = meta(*user_input_ata, false, true);
    accounts[5] = meta(*user_output_ata, false, true);
    let mut data = Vec::with_capacity(24);
    data.extend_from_slice(&[143, 190, 90, 218, 196, 30, 51, 222]);
    data.extend_from_slice(&cmd.amount_in_raw.to_le_bytes());
    data.extend_from_slice(&cmd.min_out_raw.to_le_bytes());
    Ok(Instruction {
        program_id: parse_pubkey(RAYDIUM_CPMM_PROGRAM_ID, "raydium cpmm program")?,
        accounts,
        data,
    })
}

fn parse_source_meta_accounts(accounts: &[SourcePumpSwapAccountMeta], label: &str) -> Result<Vec<AccountMeta>> {
    accounts
        .iter()
        .map(|account| {
            Ok(meta(
                parse_pubkey(&account.pubkey, label)?,
                account.is_signer,
                account.is_writable,
            ))
        })
        .collect()
}

fn parse_supported_swap_mint(value: &str, label: &str) -> Result<Pubkey> {
    parse_pubkey(value, label)
}
async fn execute_pumpfun_sell(
    cmd: ExecutePumpfunSellCommand,
    started: Instant,
) -> Result<ExecutorResponse> {
    let built = build_pumpfun_sell_tx(&cmd)?;
    let broadcast_started = Instant::now();
    let sender_path = broadcast_signed_tx(
        &BroadcastConfig::from_pumpfun_sell(&cmd),
        &built.tx,
        &built.signature,
    )
    .await?;
    Ok(ExecutorResponse {
        kind: "execResult",
        id: cmd.id,
        ok: true,
        signature: Some(built.signature),
        route: Some(cmd.route),
        sender_path: Some(sender_path),
        sol_in_lamports: Some(0),
        tokens_out_raw: Some(cmd.expected_sol_out_lamports.max(1)),
        elapsed_ms: started.elapsed().as_millis(),
        build_ms: Some(built.build_ms),
        broadcast_ms: Some(broadcast_started.elapsed().as_millis()),
        tx_bytes: Some(built.bytes),
        error: None,
    })
}

async fn execute_pumpswap_sell(
    cmd: ExecutePumpSwapSellCommand,
    started: Instant,
) -> Result<ExecutorResponse> {
    let built = build_pumpswap_sell_tx(&cmd)?;
    let broadcast_started = Instant::now();
    let sender_path = broadcast_signed_tx(
        &BroadcastConfig::from_pumpswap_sell(&cmd),
        &built.tx,
        &built.signature,
    )
    .await?;
    Ok(ExecutorResponse {
        kind: "execResult",
        id: cmd.id,
        ok: true,
        signature: Some(built.signature),
        route: Some(cmd.route),
        sender_path: Some(sender_path),
        sol_in_lamports: Some(0),
        tokens_out_raw: Some(cmd.expected_quote_out_raw.max(1)),
        elapsed_ms: started.elapsed().as_millis(),
        build_ms: Some(built.build_ms),
        broadcast_ms: Some(broadcast_started.elapsed().as_millis()),
        tx_bytes: Some(built.bytes),
        error: None,
    })
}

pub async fn execute_pumpfun_buy_command(
    cmd: ExecutePumpfunBuyCommand,
    started: Instant,
) -> Result<ExecutorResponse> {
    execute_pumpfun_buy(cmd, started).await
}

pub async fn fetch_latest_blockhash(rpc_url: &str) -> Result<String> {
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getLatestBlockhash",
        "params": [{ "commitment": "processed" }],
    });
    let response = http_client()
        .post(rpc_url)
        .timeout(Duration::from_millis(900))
        .json(&body)
        .send()
        .await
        .context("blockhash rpc send failed")?;
    let json = response
        .json::<JsonRpcResponse>()
        .await
        .context("blockhash rpc response parse failed")?;
    if let Some(error) = json.error {
        let code = error.code.map(|c| format!(" code {c}")).unwrap_or_default();
        bail!(
            "{}{}",
            error
                .message
                .unwrap_or_else(|| "blockhash json-rpc error".to_string()),
            code
        );
    }
    json.result
        .and_then(|v| v.get("value").cloned())
        .and_then(|v| {
            v.get("blockhash")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .context("getLatestBlockhash returned no blockhash")
}

fn build_pumpfun_buy_tx(cmd: &ExecutePumpfunBuyCommand) -> Result<BuiltTx> {
    let started = Instant::now();
    let private_key = env::var("PRIVATE_KEY_BASE58").context("PRIVATE_KEY_BASE58 is not set")?;
    let keypair_bytes = bs58::decode(private_key.trim())
        .into_vec()
        .context("PRIVATE_KEY_BASE58 is not valid base58")?;
    let keypair = Keypair::from_bytes(&keypair_bytes)
        .context("PRIVATE_KEY_BASE58 is not a Solana keypair")?;
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
    instructions.push(create_ata_idempotent_ix(
        &user,
        &user,
        &mint,
        &token_program_id,
    )?);
    instructions.push(build_pumpfun_buy_ix(cmd, &user, &mint, &token_program_id)?);

    let tip_sets = tip_instruction_sets(
        &user,
        cmd.jito_tip_lamports,
        cmd.helius_tip_lamports,
        cmd.use_jito_direct,
        cmd.use_jito_fallback,
    )?;
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
                last_error = Some(anyhow!(
                    "transaction too large: {bytes}B > {PACKET_DATA_SIZE}B"
                ));
            }
            Err(err) => {
                last_error = Some(err);
            }
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow!("no tip set could build a transaction")))
}

fn build_pumpswap_buy_tx(cmd: &ExecutePumpSwapBuyCommand) -> Result<BuiltTx> {
    let started = Instant::now();
    let private_key = env::var("PRIVATE_KEY_BASE58").context("PRIVATE_KEY_BASE58 is not set")?;
    let keypair_bytes = bs58::decode(private_key.trim())
        .into_vec()
        .context("PRIVATE_KEY_BASE58 is not valid base58")?;
    let keypair = Keypair::from_bytes(&keypair_bytes)
        .context("PRIVATE_KEY_BASE58 is not a Solana keypair")?;
    let user = keypair.pubkey();
    let mint = parse_pubkey(&cmd.mint, "mint")?;
    let base_token_program = parse_pubkey(&cmd.base_token_program_id, "baseTokenProgramId")?;
    let quote_token_program = parse_pubkey(TOKEN_PROGRAM_ID, "quote token program")?;
    let quote_mint = parse_supported_quote_mint(&cmd.quote_mint)?;
    let blockhash = Hash::from_str(&cmd.blockhash).context("invalid blockhash")?;
    let user_base_ata = derive_ata(&user, &mint, &base_token_program)?;
    let user_quote_ata = derive_ata(&user, &quote_mint, &quote_token_program)?;

    let mut instructions = Vec::with_capacity(8);
    if cmd.compute_unit_limit > 0 {
        instructions.push(ComputeBudgetInstruction::set_compute_unit_limit(
            cmd.compute_unit_limit.min(1_400_000),
        ));
    }
    instructions.push(ComputeBudgetInstruction::set_compute_unit_price(
        cmd.priority_fee_micro_lamports,
    ));
    instructions.push(create_ata_idempotent_ix(
        &user,
        &user,
        &mint,
        &base_token_program,
    )?);
    instructions.push(create_ata_idempotent_ix(
        &user,
        &user,
        &quote_mint,
        &quote_token_program,
    )?);
    if cmd.quote_mint == WRAPPED_SOL_MINT {
        instructions.push(system_instruction::transfer(
            &user,
            &user_quote_ata,
            cmd.quote_amount_in_raw,
        ));
        instructions.push(sync_native_ix(&user_quote_ata)?);
    }
    instructions.push(build_pumpswap_buy_ix(
        cmd,
        &user,
        &mint,
        &base_token_program,
        &user_base_ata,
        &user_quote_ata,
    )?);
    if cmd.quote_mint == WRAPPED_SOL_MINT {
        instructions.push(close_token_account_ix(&user_quote_ata, &user)?);
    }

    let tip_sets = tip_instruction_sets(
        &user,
        cmd.jito_tip_lamports,
        cmd.helius_tip_lamports,
        cmd.use_jito_direct,
        cmd.use_jito_fallback,
    )?;
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
                return Ok(BuiltTx {
                    tx,
                    signature,
                    tokens_out_raw: cmd.expected_base_out_raw.max(1),
                    bytes,
                    build_ms: started.elapsed().as_millis(),
                });
            }
            Ok((_tx, bytes)) => {
                last_error = Some(anyhow!(
                    "transaction too large: {bytes}B > {PACKET_DATA_SIZE}B"
                ));
            }
            Err(err) => {
                last_error = Some(err);
            }
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow!("no tip set could build a PumpSwap transaction")))
}

fn build_pumpfun_sell_tx(cmd: &ExecutePumpfunSellCommand) -> Result<BuiltTx> {
    let started = Instant::now();
    let private_key = env::var("PRIVATE_KEY_BASE58").context("PRIVATE_KEY_BASE58 is not set")?;
    let keypair_bytes = bs58::decode(private_key.trim())
        .into_vec()
        .context("PRIVATE_KEY_BASE58 is not valid base58")?;
    let keypair = Keypair::from_bytes(&keypair_bytes)
        .context("PRIVATE_KEY_BASE58 is not a Solana keypair")?;
    let user = keypair.pubkey();
    let mint = parse_pubkey(&cmd.mint, "mint")?;
    let blockhash = Hash::from_str(&cmd.blockhash).context("invalid blockhash")?;

    let mut instructions = Vec::with_capacity(5);
    if cmd.compute_unit_limit > 0 {
        instructions.push(ComputeBudgetInstruction::set_compute_unit_limit(
            cmd.compute_unit_limit.min(1_400_000),
        ));
    }
    instructions.push(ComputeBudgetInstruction::set_compute_unit_price(
        cmd.priority_fee_micro_lamports,
    ));
    instructions.push(build_pumpfun_sell_ix(cmd, &user, &mint)?);

    let tip_sets = tip_instruction_sets(
        &user,
        cmd.jito_tip_lamports,
        cmd.helius_tip_lamports,
        cmd.use_jito_direct,
        cmd.use_jito_fallback,
    )?;
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
                return Ok(BuiltTx {
                    tx,
                    signature,
                    tokens_out_raw: cmd.expected_sol_out_lamports.max(1),
                    bytes,
                    build_ms: started.elapsed().as_millis(),
                });
            }
            Ok((_tx, bytes)) => {
                last_error = Some(anyhow!(
                    "transaction too large: {bytes}B > {PACKET_DATA_SIZE}B"
                ));
            }
            Err(err) => last_error = Some(err),
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow!("no tip set could build a Pump.fun sell transaction")))
}

fn build_pumpswap_sell_tx(cmd: &ExecutePumpSwapSellCommand) -> Result<BuiltTx> {
    let started = Instant::now();
    let private_key = env::var("PRIVATE_KEY_BASE58").context("PRIVATE_KEY_BASE58 is not set")?;
    let keypair_bytes = bs58::decode(private_key.trim())
        .into_vec()
        .context("PRIVATE_KEY_BASE58 is not valid base58")?;
    let keypair = Keypair::from_bytes(&keypair_bytes)
        .context("PRIVATE_KEY_BASE58 is not a Solana keypair")?;
    let user = keypair.pubkey();
    let mint = parse_pubkey(&cmd.mint, "mint")?;
    let base_token_program = parse_pubkey(&cmd.base_token_program_id, "baseTokenProgramId")?;
    let token_program = parse_pubkey(TOKEN_PROGRAM_ID, "token program")?;
    let wsol = parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?;
    let blockhash = Hash::from_str(&cmd.blockhash).context("invalid blockhash")?;
    let user_base_ata = derive_ata(&user, &mint, &base_token_program)?;
    let user_quote_ata = derive_ata(&user, &wsol, &token_program)?;

    let mut instructions = Vec::with_capacity(6);
    if cmd.compute_unit_limit > 0 {
        instructions.push(ComputeBudgetInstruction::set_compute_unit_limit(
            cmd.compute_unit_limit.min(1_400_000),
        ));
    }
    instructions.push(ComputeBudgetInstruction::set_compute_unit_price(
        cmd.priority_fee_micro_lamports,
    ));
    instructions.push(create_ata_idempotent_ix(
        &user,
        &user,
        &wsol,
        &token_program,
    )?);
    instructions.push(build_pumpswap_sell_ix(
        cmd,
        &user,
        &mint,
        &base_token_program,
        &user_base_ata,
        &user_quote_ata,
    )?);
    instructions.push(close_token_account_ix(&user_quote_ata, &user)?);

    let tip_sets = tip_instruction_sets(
        &user,
        cmd.jito_tip_lamports,
        cmd.helius_tip_lamports,
        cmd.use_jito_direct,
        cmd.use_jito_fallback,
    )?;
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
                return Ok(BuiltTx {
                    tx,
                    signature,
                    tokens_out_raw: cmd.expected_quote_out_raw.max(1),
                    bytes,
                    build_ms: started.elapsed().as_millis(),
                });
            }
            Ok((_tx, bytes)) => {
                last_error = Some(anyhow!(
                    "transaction too large: {bytes}B > {PACKET_DATA_SIZE}B"
                ));
            }
            Err(err) => {
                last_error = Some(err);
            }
        }
    }
    Err(last_error.unwrap_or_else(|| anyhow!("no tip set could build a PumpSwap sell transaction")))
}

fn compile_and_sign(
    keypair: &Keypair,
    blockhash: Hash,
    instructions: &[Instruction],
) -> Result<(VersionedTransaction, usize)> {
    let payer = keypair.pubkey();
    let message = v0::Message::try_compile(&payer, instructions, &[], blockhash)
        .context("compile v0 message failed")?;
    let tx = VersionedTransaction::try_new(VersionedMessage::V0(message), &[keypair])
        .context("sign v0 transaction failed")?;
    let bytes = bincode::serialize(&tx)
        .context("serialize transaction failed")?
        .len();
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
        let quote_mint = source_replay_quote_mint(cmd, &source_accounts, mint)?;
        return build_source_replay_buy_ix(
            &source_accounts,
            &source_data_for_scaled(&source_data, &scaled),
            user,
            mint,
            token_program_id,
            &quote_mint,
            exact_sol_in,
        );
    }

    let amount = if cmd.exact_sol_in {
        let tokens = cmd
            .tokens_out_raw
            .context("direct rust buy requires tokensOutRaw")?;
        tokens.saturating_mul(10_000u64.saturating_sub(cmd.slippage_bps.min(10_000))) / 10_000
    } else {
        cmd.tokens_out_raw
            .context("direct rust buy requires tokensOutRaw")?
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
        if is_v2 {
            return build_source_replay_buy_ix(
                &source_accounts,
                &data,
                user,
                mint,
                token_program_id,
                &parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?,
                cmd.exact_sol_in,
            );
        }
        if !is_token_2022 && source_accounts.len() >= 16 {
            return build_source_replay_buy_ix(
                &source_accounts,
                &data,
                user,
                mint,
                token_program_id,
                &parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?,
                cmd.exact_sol_in,
            );
        }
    }

    build_derived_buy_ix(cmd, user, mint, token_program_id, data)
}

fn build_pumpswap_buy_ix(
    cmd: &ExecutePumpSwapBuyCommand,
    user: &Pubkey,
    mint: &Pubkey,
    base_token_program: &Pubkey,
    user_base_ata: &Pubkey,
    user_quote_ata: &Pubkey,
) -> Result<Instruction> {
    let source_accounts = parse_pumpswap_account_pubkeys(&cmd.source_pump_swap_accounts)?;
    if source_accounts.len() < PUMPSWAP_MIN_BUY_ACCOUNT_COUNT {
        bail!("PumpSwap account list too short: {}", source_accounts.len());
    }
    if source_accounts.get(PUMPSWAP_BASE_MINT_INDEX) != Some(mint) {
        bail!("source PumpSwap accounts do not match target base mint");
    }
    let quote_mint = parse_supported_quote_mint(&cmd.quote_mint)?;
    if source_accounts.get(PUMPSWAP_QUOTE_MINT_INDEX) != Some(&quote_mint) {
        bail!("source PumpSwap accounts do not match requested quote mint");
    }
    if source_accounts.get(PUMPSWAP_BASE_TOKEN_PROGRAM_INDEX) != Some(base_token_program) {
        bail!("source PumpSwap accounts do not match base token program");
    }

    let user_volume_accumulator = derive_pumpswap_user_volume_accumulator(user)?;
    let keys = cmd
        .source_pump_swap_accounts
        .iter()
        .enumerate()
        .map(|(i, source_meta)| {
            let source = source_accounts
                .get(i)
                .copied()
                .ok_or_else(|| anyhow!("source PumpSwap account index {i} missing"))?;
            let actual = match i {
                PUMPSWAP_USER_INDEX => *user,
                PUMPSWAP_USER_BASE_ATA_INDEX => *user_base_ata,
                PUMPSWAP_USER_QUOTE_ATA_INDEX => *user_quote_ata,
                PUMPSWAP_USER_VOLUME_ACCUMULATOR_INDEX => user_volume_accumulator,
                _ => source,
            };
            let is_signer = i == PUMPSWAP_USER_INDEX;
            let is_writable = source_meta.is_writable || pumpswap_canonical_buy_writable(i);
            Ok(meta(actual, is_signer, is_writable))
        })
        .collect::<Result<Vec<_>>>()?;

    Ok(Instruction {
        program_id: parse_pubkey(PUMPSWAP_PROGRAM_ID, "pumpswap program")?,
        accounts: keys,
        data: encode_pumpswap_buy_args(cmd),
    })
}

fn build_pumpfun_sell_ix(
    cmd: &ExecutePumpfunSellCommand,
    user: &Pubkey,
    mint: &Pubkey,
) -> Result<Instruction> {
    let (min_accounts, user_index, mint_index, program_index) = if cmd.sell_v2 {
        (26usize, 13usize, 1usize, 25usize)
    } else {
        (14usize, 6usize, 2usize, 11usize)
    };
    if cmd.accounts.len() < min_accounts {
        bail!(
            "Pump.fun sell account list too short: {} < {}",
            cmd.accounts.len(),
            min_accounts
        );
    }
    let program_id = parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump.fun program")?;
    let parsed = cmd
        .accounts
        .iter()
        .map(|account| parse_pubkey(&account.pubkey, "Pump.fun sell account"))
        .collect::<Result<Vec<_>>>()?;
    if parsed.get(mint_index) != Some(mint) {
        bail!("Pump.fun sell accounts do not match target mint");
    }
    if parsed.get(program_index) != Some(&program_id) {
        bail!("Pump.fun sell accounts do not contain canonical program account");
    }

    let accounts = cmd
        .accounts
        .iter()
        .enumerate()
        .map(|(index, account)| {
            if index != user_index && account.is_signer {
                bail!("unexpected signer at Pump.fun sell account index {index}");
            }
            let pubkey = if index == user_index {
                *user
            } else {
                parsed[index]
            };
            Ok(meta(
                pubkey,
                index == user_index,
                if index == user_index {
                    true
                } else {
                    account.is_writable
                },
            ))
        })
        .collect::<Result<Vec<_>>>()?;

    Ok(Instruction {
        program_id,
        accounts,
        data: encode_pumpfun_sell_data(cmd.sell_v2, cmd.tokens_in_raw, cmd.min_sol_out_lamports),
    })
}

fn encode_pumpfun_sell_data(
    sell_v2: bool,
    tokens_in_raw: u64,
    min_sol_out_lamports: u64,
) -> Vec<u8> {
    let mut data = Vec::with_capacity(if sell_v2 { 24 } else { 25 });
    data.extend_from_slice(if sell_v2 {
        &PUMP_FUN_SELL_V2_DISCRIMINATOR
    } else {
        &PUMP_FUN_SELL_DISCRIMINATOR
    });
    data.extend_from_slice(&tokens_in_raw.to_le_bytes());
    data.extend_from_slice(&min_sol_out_lamports.to_le_bytes());
    if !sell_v2 {
        data.push(0); // OptionBool::None for track_volume.
    }
    data
}

fn build_pumpswap_sell_ix(
    cmd: &ExecutePumpSwapSellCommand,
    user: &Pubkey,
    mint: &Pubkey,
    base_token_program: &Pubkey,
    user_base_ata: &Pubkey,
    user_quote_ata: &Pubkey,
) -> Result<Instruction> {
    let source_accounts = parse_pumpswap_account_pubkeys(&cmd.source_pump_swap_accounts)?;
    if source_accounts.len() < PUMPSWAP_MIN_SELL_ACCOUNT_COUNT {
        bail!(
            "PumpSwap sell account list too short: {}",
            source_accounts.len()
        );
    }
    if source_accounts.get(PUMPSWAP_BASE_MINT_INDEX) != Some(mint) {
        bail!("source PumpSwap sell accounts do not match target base mint");
    }
    if source_accounts.get(PUMPSWAP_QUOTE_MINT_INDEX)
        != Some(&parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?)
    {
        bail!("source PumpSwap sell accounts do not match SOL quote mint");
    }
    if source_accounts.get(PUMPSWAP_BASE_TOKEN_PROGRAM_INDEX) != Some(base_token_program) {
        bail!("source PumpSwap sell accounts do not match base token program");
    }

    // NOTE: unlike buys, the PumpSwap SELL layout has no user_volume_accumulator.
    // In the current on-chain layout index 20 is fee_program, so we must NOT
    // substitute it — only the user, user base ATA and user quote ATA are
    // user-specific; everything else (incl. the per-token fee-program accounts)
    // is carried verbatim from the template.
    let template_user = source_accounts[PUMPSWAP_USER_INDEX];
    let template_volume = derive_pumpswap_user_volume_accumulator(&template_user)?;
    let wsol = parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?;
    let token_program = parse_pubkey(TOKEN_PROGRAM_ID, "token program")?;
    let template_cashback_ata = derive_ata(&template_volume, &wsol, &token_program)?;
    let has_cashback_sell_tail = source_accounts.get(PUMPSWAP_SELL_CASHBACK_ATA_INDEX)
        == Some(&template_cashback_ata)
        && source_accounts.get(PUMPSWAP_SELL_CASHBACK_USER_VOLUME_INDEX) == Some(&template_volume);
    let user_volume = derive_pumpswap_user_volume_accumulator(user)?;
    let user_cashback_ata = derive_ata(&user_volume, &wsol, &token_program)?;
    let keys = cmd
        .source_pump_swap_accounts
        .iter()
        .enumerate()
        .map(|(i, source_meta)| {
            let source = source_accounts
                .get(i)
                .copied()
                .ok_or_else(|| anyhow!("source PumpSwap sell account index {i} missing"))?;
            let actual = match i {
                PUMPSWAP_USER_INDEX => *user,
                PUMPSWAP_USER_BASE_ATA_INDEX => *user_base_ata,
                PUMPSWAP_USER_QUOTE_ATA_INDEX => *user_quote_ata,
                PUMPSWAP_SELL_CASHBACK_ATA_INDEX if has_cashback_sell_tail => user_cashback_ata,
                PUMPSWAP_SELL_CASHBACK_USER_VOLUME_INDEX if has_cashback_sell_tail => user_volume,
                _ => source,
            };
            let is_signer = i == PUMPSWAP_USER_INDEX;
            let is_writable = source_meta.is_writable || pumpswap_canonical_sell_writable(i, has_cashback_sell_tail);
            Ok(meta(actual, is_signer, is_writable))
        })
        .collect::<Result<Vec<_>>>()?;

    Ok(Instruction {
        program_id: parse_pubkey(PUMPSWAP_PROGRAM_ID, "pumpswap program")?,
        accounts: keys,
        data: encode_pumpswap_sell_args(cmd),
    })
}

fn encode_pumpswap_buy_args(cmd: &ExecutePumpSwapBuyCommand) -> Vec<u8> {
    encode_pumpswap_buy_data(
        &cmd.buy_instruction,
        cmd.quote_amount_in_raw,
        cmd.min_base_out_raw,
    )
}

fn encode_pumpswap_buy_data(
    buy_instruction: &str,
    quote_amount_in_raw: u64,
    min_base_out_raw: u64,
) -> Vec<u8> {
    let mut data = vec![0u8; 25];
    if buy_instruction == "buy" {
        data[..8].copy_from_slice(&BUY_DISCRIMINATOR);
        data[8..16].copy_from_slice(&min_base_out_raw.max(1).to_le_bytes());
        data[16..24].copy_from_slice(&quote_amount_in_raw.to_le_bytes());
    } else {
        data[..8].copy_from_slice(&PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR);
        data[8..16].copy_from_slice(&quote_amount_in_raw.to_le_bytes());
        data[16..24].copy_from_slice(&min_base_out_raw.max(1).to_le_bytes());
    }
    data[24] = 0;
    data
}

fn encode_pumpswap_sell_args(cmd: &ExecutePumpSwapSellCommand) -> Vec<u8> {
    let mut data = vec![0u8; 24];
    data[..8].copy_from_slice(&PUMPSWAP_SELL_DISCRIMINATOR);
    data[8..16].copy_from_slice(&cmd.tokens_in_raw.max(1).to_le_bytes());
    data[16..24].copy_from_slice(&cmd.min_quote_out_raw.max(1).to_le_bytes());
    data
}

struct ScaledBuyData {
    exact_sol_in: bool,
    amount: u64,
    max_sol_cost: u64,
}

fn scale_source_buy_data(
    data: &[u8],
    sol_in_lamports: u64,
    slippage_bps: u64,
) -> Result<ScaledBuyData> {
    let bps = slippage_bps.min(10_000);
    if starts_with(data, &BUY_DISCRIMINATOR) || starts_with(data, &BUY_V2_DISCRIMINATOR) {
        let source_tokens_out =
            read_u64_le(data, 8).context("source buy data missing token amount")?;
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
        let source_min_tokens_out =
            read_u64_le(data, 16).context("source exact-in data missing min out")?;
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
    data.resize(
        if starts_with(&discriminator, &BUY_DISCRIMINATOR)
            || starts_with(&discriminator, &BUY_EXACT_SOL_IN_DISCRIMINATOR)
        {
            25
        } else {
            24
        },
        0,
    );
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
    quote_mint: &Pubkey,
    exact_sol_in: bool,
) -> Result<Instruction> {
    let program_id = parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?;
    let associated_user = derive_ata(user, mint, token_program_id)?;
    let user_volume_accumulator = derive_user_volume_accumulator(user)?;
    let quote_token_program = parse_pubkey(TOKEN_PROGRAM_ID, "quote token program")?;
    let associated_quote_user = derive_ata(user, quote_mint, &quote_token_program)?;
    let associated_user_volume_accumulator =
        derive_ata(&user_volume_accumulator, quote_mint, &quote_token_program)?;

    let mut keys = Vec::new();
    if is_v2_source_accounts(source_accounts, mint) {
        for (i, source) in source_accounts
            .iter()
            .take(PUMP_FUN_BUY_V2_ACCOUNT_COUNT)
            .enumerate()
        {
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
    let bonding_curve = parse_pubkey(
        cmd.bonding_curve
            .as_deref()
            .context("direct buy missing bondingCurve")?,
        "bondingCurve",
    )?;
    let creator = parse_pubkey(
        cmd.creator
            .as_deref()
            .context("direct buy missing creator")?,
        "creator",
    )?;
    let associated_bonding_curve = derive_ata(&bonding_curve, mint, token_program_id)?;
    let associated_user = derive_ata(user, mint, token_program_id)?;
    let creator_vault = derive_creator_vault(&creator)?;
    let keys = vec![
        meta(derive_global()?, false, false),
        meta(
            parse_pubkey(PUMP_FUN_FEE_RECIPIENT, "fee recipient")?,
            false,
            true,
        ),
        meta(*mint, false, false),
        meta(bonding_curve, false, true),
        meta(associated_bonding_curve, false, true),
        meta(associated_user, false, true),
        meta(*user, true, true),
        meta(solana_system_interface::program::ID, false, false),
        meta(*token_program_id, false, false),
        meta(creator_vault, false, true),
        meta(derive_event_authority()?, false, false),
        meta(
            parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?,
            false,
            false,
        ),
        meta(derive_global_volume_accumulator()?, false, cmd.exact_sol_in),
        meta(derive_user_volume_accumulator(user)?, false, true),
        meta(derive_fee_config()?, false, false),
        meta(
            parse_pubkey(PUMP_FUN_FEE_PROGRAM_ID, "fee program")?,
            false,
            false,
        ),
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
    let bonding_curve = parse_pubkey(
        cmd.bonding_curve
            .as_deref()
            .context("direct buy missing bondingCurve")?,
        "bondingCurve",
    )?;
    let creator = parse_pubkey(
        cmd.creator
            .as_deref()
            .context("direct buy missing creator")?,
        "creator",
    )?;
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
        meta(
            parse_pubkey(ASSOCIATED_TOKEN_PROGRAM_ID, "ata program")?,
            false,
            false,
        ),
        meta(
            parse_pubkey(PUMP_FUN_FEE_RECIPIENT, "fee recipient")?,
            false,
            true,
        ),
        meta(
            derive_ata(
                &parse_pubkey(PUMP_FUN_FEE_RECIPIENT, "fee recipient")?,
                &wsol,
                &token_program,
            )?,
            false,
            true,
        ),
        meta(
            parse_pubkey(PUMP_FUN_BUYBACK_FEE_RECIPIENT, "buyback recipient")?,
            false,
            true,
        ),
        meta(
            derive_ata(
                &parse_pubkey(PUMP_FUN_BUYBACK_FEE_RECIPIENT, "buyback recipient")?,
                &wsol,
                &token_program,
            )?,
            false,
            true,
        ),
        meta(bonding_curve, false, true),
        meta(
            derive_ata(&bonding_curve, mint, token_program_id)?,
            false,
            true,
        ),
        meta(
            derive_ata(&bonding_curve, &wsol, &token_program)?,
            false,
            true,
        ),
        meta(*user, true, true),
        meta(derive_ata(user, mint, token_program_id)?, false, true),
        meta(derive_ata(user, &wsol, &token_program)?, false, true),
        meta(creator_vault, false, true),
        meta(
            derive_ata(&creator_vault, &wsol, &token_program)?,
            false,
            true,
        ),
        meta(derive_sharing_config(mint)?, false, false),
        meta(derive_global_volume_accumulator()?, false, false),
        meta(user_volume_accumulator, false, true),
        meta(
            derive_ata(&user_volume_accumulator, &wsol, &token_program)?,
            false,
            true,
        ),
        meta(derive_fee_config()?, false, false),
        meta(
            parse_pubkey(PUMP_FUN_FEE_PROGRAM_ID, "fee program")?,
            false,
            false,
        ),
        meta(solana_system_interface::program::ID, false, false),
        meta(derive_event_authority()?, false, false),
        meta(
            parse_pubkey(PUMP_FUN_PROGRAM_ID, "pump program")?,
            false,
            false,
        ),
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

fn sync_native_ix(account: &Pubkey) -> Result<Instruction> {
    Ok(Instruction {
        program_id: parse_pubkey(TOKEN_PROGRAM_ID, "token program")?,
        accounts: vec![meta(*account, false, true)],
        data: SYNC_NATIVE_DISCRIMINATOR.to_vec(),
    })
}

fn close_token_account_ix(account: &Pubkey, owner: &Pubkey) -> Result<Instruction> {
    Ok(Instruction {
        program_id: parse_pubkey(TOKEN_PROGRAM_ID, "token program")?,
        accounts: vec![
            meta(*account, false, true),
            meta(*owner, false, true),
            meta(*owner, true, false),
        ],
        data: CLOSE_ACCOUNT_DISCRIMINATOR.to_vec(),
    })
}

fn tip_instruction_sets(
    user: &Pubkey,
    jito_tip_lamports: u64,
    helius_tip_lamports: u64,
    use_jito_direct: bool,
    use_jito_fallback: bool,
) -> Result<Vec<Vec<Instruction>>> {
    let mut all = Vec::new();
    if use_jito_direct && jito_tip_lamports > 0 {
        all.push(system_instruction::transfer(
            user,
            &parse_pubkey(JITO_TIP_ACCOUNTS[0], "jito tip account")?,
            jito_tip_lamports,
        ));
    }
    if (!use_jito_direct || use_jito_fallback) && helius_tip_lamports > 0 {
        all.push(system_instruction::transfer(
            user,
            &parse_pubkey(HELIUS_TIP_ACCOUNTS[0], "helius tip account")?,
            helius_tip_lamports,
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

async fn broadcast_signed_tx(
    cfg: &BroadcastConfig,
    tx: &VersionedTransaction,
    signature: &str,
) -> Result<String> {
    let tx_base64 =
        BASE64_STANDARD.encode(bincode::serialize(tx).context("serialize tx for broadcast")?);
    let candidates = broadcast_candidates(cfg)?;
    if cfg.execution_multi_send_enabled {
        return race_broadcast(
            candidates,
            tx_base64,
            signature.to_string(),
            cfg.jito_auth_uuid.clone(),
        )
        .await;
    }

    let client = http_client();
    let mut errors = Vec::new();
    for candidate in candidates {
        match send_candidate(
            &client,
            &candidate,
            &tx_base64,
            signature,
            cfg.jito_auth_uuid.as_deref(),
        )
        .await
        {
            Ok(path) => return Ok(path),
            Err(err) => errors.push(format!("{}: {err}", candidate.name)),
        }
    }
    Err(anyhow!(
        "all rust executor broadcast paths failed: {}",
        errors.join(" | ")
    ))
}

fn broadcast_candidates(cfg: &BroadcastConfig) -> Result<Vec<BroadcastCandidate>> {
    let mut candidates = Vec::new();
    if cfg.use_jito_direct {
        match cfg.jito_send_mode.as_str() {
            "transaction" | "tx" => {
                for url in &cfg.jito_transaction_urls {
                    candidates.push(BroadcastCandidate {
                        name: "jito-tx".to_string(),
                        url: url.clone(),
                        method: BroadcastMethod::JitoTransaction,
                    });
                }
            }
            "race" | "both" => {
                for url in &cfg.jito_transaction_urls {
                    candidates.push(BroadcastCandidate {
                        name: "jito-tx".to_string(),
                        url: url.clone(),
                        method: BroadcastMethod::JitoTransaction,
                    });
                }
                for url in &cfg.jito_bundle_urls {
                    candidates.push(BroadcastCandidate {
                        name: "jito-bundle".to_string(),
                        url: url.clone(),
                        method: BroadcastMethod::JitoBundle,
                    });
                }
            }
            _ => {
                for url in &cfg.jito_bundle_urls {
                    candidates.push(BroadcastCandidate {
                        name: "jito-bundle".to_string(),
                        url: url.clone(),
                        method: BroadcastMethod::JitoBundle,
                    });
                }
            }
        }
    }
    if !cfg.use_jito_direct || cfg.use_jito_fallback {
        candidates.push(BroadcastCandidate {
            name: "helius-sender".to_string(),
            url: cfg.sender_endpoint_url.clone(),
            method: BroadcastMethod::HeliusSender,
        });
    }
    if cfg.sender_rpc_fallback_enabled && !cfg.rpc_fallback_url.trim().is_empty() {
        candidates.push(BroadcastCandidate {
            name: "rpc-fallback".to_string(),
            url: cfg.rpc_fallback_url.clone(),
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
    let client = http_client();
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
    Err(anyhow!(
        "all rust executor broadcast paths failed: {}",
        errors.join(" | ")
    ))
}

async fn send_candidate(
    client: &reqwest::Client,
    candidate: &BroadcastCandidate,
    tx_base64: &str,
    signature: &str,
    jito_auth_uuid: Option<&str>,
) -> Result<String> {
    let (method, params) = match candidate.method {
        BroadcastMethod::JitoBundle => {
            ("sendBundle", json!([[tx_base64], { "encoding": "base64" }]))
        }
        BroadcastMethod::JitoTransaction
        | BroadcastMethod::HeliusSender
        | BroadcastMethod::RpcFallback => (
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
    let json = response
        .json::<JsonRpcResponse>()
        .await
        .context("json-rpc response parse failed")?;
    if let Some(error) = json.error {
        let code = error.code.map(|c| format!(" code {c}")).unwrap_or_default();
        bail!(
            "{}{}",
            error
                .message
                .unwrap_or_else(|| "json-rpc error".to_string()),
            code
        );
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
        return Ok(
            scale_source_buy_data(&source_data, cmd.sol_in_lamports, cmd.slippage_bps)?.amount,
        );
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
            return parsed
                .get(3)
                .copied()
                .context("source V2 accounts missing token program");
        }
        return parsed
            .get(8)
            .copied()
            .context("source legacy accounts missing token program");
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
        .decode(
            cmd.source_ix_data_base64
                .as_deref()
                .context("source-replay requires sourceIxDataBase64")?,
        )
        .context("sourceIxDataBase64 decode failed")
}

fn parse_pubkeys(values: &[String], label: &str) -> Result<Vec<Pubkey>> {
    values
        .iter()
        .map(|value| parse_pubkey(value, label))
        .collect::<Result<Vec<_>>>()
}

fn parse_pumpswap_account_pubkeys(values: &[SourcePumpSwapAccountMeta]) -> Result<Vec<Pubkey>> {
    values
        .iter()
        .map(|value| parse_pubkey(&value.pubkey, "sourcePumpSwapAccounts"))
        .collect::<Result<Vec<_>>>()
}

fn parse_pubkey(value: &str, label: &str) -> Result<Pubkey> {
    Pubkey::from_str(value).with_context(|| format!("invalid {label} pubkey: {value}"))
}

fn source_replay_quote_mint(
    cmd: &ExecutePumpfunBuyCommand,
    source_accounts: &[Pubkey],
    mint: &Pubkey,
) -> Result<Pubkey> {
    let account_quote = if is_v2_source_accounts(source_accounts, mint) {
        source_accounts
            .get(2)
            .copied()
            .context("source V2 accounts missing quote mint")?
    } else {
        parse_pubkey(WRAPPED_SOL_MINT, "wsol mint")?
    };
    let account_quote_text = account_quote.to_string();
    parse_supported_quote_mint(&account_quote_text)?;
    if let Some(command_quote) = cmd.quote_mint.as_deref() {
        if parse_supported_quote_mint(command_quote)? != account_quote {
            bail!("source Pump.fun quote mint does not match command quote mint");
        }
    }
    Ok(account_quote)
}

fn parse_supported_quote_mint(value: &str) -> Result<Pubkey> {
    if value != WRAPPED_SOL_MINT && value != USDC_MINT {
        bail!("unsupported PumpSwap quote mint: {value}");
    }
    parse_pubkey(value, "quoteMint")
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
    pda(
        &[b"user_volume_accumulator", user.as_ref()],
        PUMP_FUN_PROGRAM_ID,
    )
}

fn derive_pumpswap_user_volume_accumulator(user: &Pubkey) -> Result<Pubkey> {
    pda(
        &[b"user_volume_accumulator", user.as_ref()],
        PUMPSWAP_PROGRAM_ID,
    )
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pumpswap_accepts_only_supported_quote_mints() {
        assert!(parse_supported_quote_mint(WRAPPED_SOL_MINT).is_ok());
        assert!(parse_supported_quote_mint(USDC_MINT).is_ok());
        assert!(parse_supported_quote_mint("11111111111111111111111111111111").is_err());
    }

    #[test]
    fn pumpswap_exact_quote_in_encodes_raw_usdc_units() {
        let quote_raw = 12_345_678u64;
        let min_base_out = 987_654_321u64;
        let data = encode_pumpswap_buy_data("buy_exact_quote_in", quote_raw, min_base_out);
        assert_eq!(&data[..8], &PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR);
        assert_eq!(
            u64::from_le_bytes(data[8..16].try_into().unwrap()),
            quote_raw
        );
        assert_eq!(
            u64::from_le_bytes(data[16..24].try_into().unwrap()),
            min_base_out
        );
        assert_eq!(data[24], 0);
    }

    #[test]
    fn pumpfun_sell_encodes_legacy_and_v2_layouts() {
        let legacy = encode_pumpfun_sell_data(false, 123, 456);
        assert_eq!(legacy.len(), 25);
        assert_eq!(&legacy[..8], &PUMP_FUN_SELL_DISCRIMINATOR);
        assert_eq!(u64::from_le_bytes(legacy[8..16].try_into().unwrap()), 123);
        assert_eq!(u64::from_le_bytes(legacy[16..24].try_into().unwrap()), 456);
        assert_eq!(legacy[24], 0);

        let v2 = encode_pumpfun_sell_data(true, 789, 321);
        assert_eq!(v2.len(), 24);
        assert_eq!(&v2[..8], &PUMP_FUN_SELL_V2_DISCRIMINATOR);
        assert_eq!(u64::from_le_bytes(v2[8..16].try_into().unwrap()), 789);
        assert_eq!(u64::from_le_bytes(v2[16..24].try_into().unwrap()), 321);
    }

    #[test]
    fn pumpfun_sell_command_deserializes_from_bridge_shape() {
        let value = json!({
            "kind": "executePumpfunSell",
            "id": "sell-1",
            "route": "pumpfun-direct-sell",
            "mint": "11111111111111111111111111111111",
            "tokensInRaw": 123,
            "expectedSolOutLamports": 456,
            "minSolOutLamports": 400,
            "sellV2": false,
            "accounts": [],
            "blockhash": "11111111111111111111111111111111",
            "computeUnitLimit": 200000,
            "priorityFeeMicroLamports": 1,
            "jitoTipLamports": 0,
            "heliusTipLamports": 0,
            "useJitoDirect": false,
            "useJitoFallback": false,
            "senderRpcFallbackEnabled": true,
            "executionMultiSendEnabled": false,
            "jitoSendMode": "transaction",
            "jitoTransactionUrls": [],
            "jitoBundleUrls": [],
            "senderEndpointUrl": "http://localhost",
            "rpcFallbackUrl": "http://localhost",
            "jitoAuthUuid": null
        });
        let parsed = serde_json::from_value::<ExecutorCommand>(value).unwrap();
        assert!(matches!(parsed, ExecutorCommand::ExecutePumpfunSell(_)));
    }
}


