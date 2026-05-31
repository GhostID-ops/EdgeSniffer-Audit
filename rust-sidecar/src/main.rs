use std::{collections::HashMap, env, time::Duration};

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use futures::{stream, StreamExt};
use serde::Serialize;
use tokio::time::sleep;
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient,
    subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest,
    SubscribeRequestFilterTransactions,
};
use yellowstone_grpc_proto::tonic::{
    metadata::AsciiMetadataValue,
    transport::{Channel, ClientTlsConfig},
    Request, Status,
};

const WRAPPED_SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const PUMP_FUN_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMPSWAP_PROGRAM_ID: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const BUY_DISCRIMINATOR: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR: [u8; 8] =
    [198, 46, 21, 82, 180, 217, 232, 112];
const BUY_EXACT_SOL_IN_DISCRIMINATOR: [u8; 8] = [56, 252, 116, 8, 158, 223, 205, 95];
const BUY_V2_DISCRIMINATOR: [u8; 8] = [184, 23, 238, 97, 103, 197, 211, 61];
const BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];
const SELL_DISCRIMINATOR: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const SELL_V2_DISCRIMINATOR: [u8; 8] = [93, 246, 130, 60, 231, 233, 64, 178];

#[derive(Debug, Clone)]
struct Config {
    endpoint: String,
    x_token: String,
    tracked_wallets: Vec<String>,
    reconnect_ms: u64,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct DetectedSwapLine {
    kind: &'static str,
    source: &'static str,
    detected_at_ms: u128,
    signature: String,
    wallet: String,
    input_mint: String,
    output_mint: String,
    amount_raw: u64,
    is_native_input: bool,
    counterparty_amount_raw: Option<u64>,
    source_ix_accounts: Option<Vec<String>>,
    source_ix_data_base64: Option<String>,
    source_pump_swap_accounts: Option<Vec<String>>,
    source_quote_mint: Option<String>,
    source_quote_amount_raw: Option<u64>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let cfg = Config::from_env()?;
    eprintln!(
        "rust-sidecar: starting endpoint={} wallets={}",
        cfg.endpoint,
        cfg.tracked_wallets.len()
    );

    loop {
        if let Err(err) = run_once(&cfg).await {
            eprintln!(
                "rust-sidecar: stream error: {err:#}; reconnecting in {}ms",
                cfg.reconnect_ms
            );
            sleep(Duration::from_millis(cfg.reconnect_ms)).await;
            continue;
        }
    }
}

impl Config {
    fn from_env() -> Result<Self> {
        let endpoint = env::var("RUST_SIDECAR_GRPC_URL")
            .or_else(|_| env::var("SHYFT_RABBITSTREAM_URL"))
            .or_else(|_| env::var("SHYFT_GRPC_URL"))
            .context("RUST_SIDECAR_GRPC_URL / SHYFT_RABBITSTREAM_URL / SHYFT_GRPC_URL is not set")?;
        let x_token = env::var("SHYFT_GRPC_X_TOKEN")
            .or_else(|_| env::var("SHYFT_GRPC_TOKEN"))
            .context("SHYFT_GRPC_X_TOKEN is not set")?;
        let tracked_wallets = env::var("TRACKED_WALLETS")
            .unwrap_or_default()
            .split(',')
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect::<Vec<_>>();
        if tracked_wallets.is_empty() {
            bail!("TRACKED_WALLETS is empty");
        }
        let reconnect_ms = env::var("SHYFT_GRPC_RECONNECT_MS")
            .ok()
            .and_then(|v| v.parse::<u64>().ok())
            .unwrap_or(1000)
            .max(250);
        Ok(Self {
            endpoint,
            x_token,
            tracked_wallets,
            reconnect_ms,
        })
    }
}

async fn run_once(cfg: &Config) -> Result<()> {
    let token: AsciiMetadataValue = cfg
        .x_token
        .parse()
        .context("failed to parse SHYFT_GRPC_X_TOKEN as metadata")?;
    let channel = Channel::from_shared(cfg.endpoint.clone())?
        .tls_config(ClientTlsConfig::new().with_native_roots())?
        .connect()
        .await?;
    let mut client = GeyserClient::with_interceptor(channel, move |mut req: Request<()>| {
        req.metadata_mut().insert("x-token", token.clone());
        Ok::<_, Status>(req)
    })
    .max_decoding_message_size(64 * 1024 * 1024);

    let request = build_request(&cfg.tracked_wallets);
    let requests = stream::once(async move { request }).chain(stream::pending());
    let mut stream = client.subscribe(Request::new(requests)).await?.into_inner();
    eprintln!("rust-sidecar: subscribed");

    while let Some(update) = stream.next().await {
        let update = update?;
        let Some(UpdateOneof::Transaction(tx_update)) = update.update_oneof else {
            continue;
        };
        let Some(info) = tx_update.transaction else {
            continue;
        };
        if let Some(line) = extract_swap(&info, &cfg.tracked_wallets) {
            println!("{}", serde_json::to_string(&line)?);
        }
    }

    Ok(())
}

fn build_request(tracked_wallets: &[String]) -> SubscribeRequest {
    let mut transactions = HashMap::new();
    transactions.insert(
        "trackedWallets".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            signature: None,
            account_include: tracked_wallets.to_vec(),
            account_exclude: vec![],
            account_required: vec![],
        },
    );

    SubscribeRequest {
        accounts: HashMap::new(),
        slots: HashMap::new(),
        transactions,
        transactions_status: HashMap::new(),
        blocks: HashMap::new(),
        blocks_meta: HashMap::new(),
        entry: HashMap::new(),
        accounts_data_slice: vec![],
        commitment: Some(CommitmentLevel::Processed as i32),
        ping: None,
        from_slot: None,
    }
}

fn extract_swap(
    info: &yellowstone_grpc_proto::geyser::SubscribeUpdateTransactionInfo,
    tracked_wallets: &[String],
) -> Option<DetectedSwapLine> {
    let message = info.transaction.as_ref()?.message.as_ref()?;
    let account_keys = message
        .account_keys
        .iter()
        .map(|key| bs58::encode(key).into_string())
        .collect::<Vec<_>>();
    let wallet = tracked_wallets
        .iter()
        .find(|wallet| account_keys.iter().any(|key| key == *wallet))?
        .clone();
    let signature = bs58::encode(&info.signature).into_string();

    for ix in &message.instructions {
        let program_id = account_keys.get(ix.program_id_index as usize)?;
        if program_id == PUMPSWAP_PROGRAM_ID {
            if let Some(line) =
                extract_top_level_pumpswap_buy(ix, &account_keys, &signature, &wallet)
            {
                return Some(line);
            }
            continue;
        }
        if program_id != PUMP_FUN_PROGRAM_ID {
            continue;
        }
        let source_ix_accounts = ix
            .accounts
            .iter()
            .filter_map(|idx| account_keys.get(*idx as usize).cloned())
            .collect::<Vec<_>>();
        let is_legacy = source_ix_accounts.len() >= 16
            && source_ix_accounts.get(2).is_some()
            && source_ix_accounts.get(11).is_some_and(|v| v == PUMP_FUN_PROGRAM_ID);
        let is_v2 = source_ix_accounts.len() >= 27
            && source_ix_accounts.get(1).is_some()
            && source_ix_accounts.get(26).is_some_and(|v| v == PUMP_FUN_PROGRAM_ID);
        if !is_legacy && !is_v2 {
            continue;
        }
        let mint = if is_v2 {
            source_ix_accounts.get(1)?.clone()
        } else {
            source_ix_accounts.get(2)?.clone()
        };
        let data = ix.data.as_slice();

        if starts_with(data, &BUY_DISCRIMINATOR) || starts_with(data, &BUY_V2_DISCRIMINATOR) {
            let max_sol_cost = read_u64_le(data, 16)?;
            if max_sol_cost == 0 {
                continue;
            }
            return Some(DetectedSwapLine {
                kind: "swap",
                source: "rust-sidecar",
                detected_at_ms: now_ms(),
                signature,
                wallet,
                input_mint: WRAPPED_SOL_MINT.to_string(),
                output_mint: mint,
                amount_raw: max_sol_cost,
                is_native_input: true,
                counterparty_amount_raw: None,
                source_ix_accounts: Some(source_ix_accounts),
                source_ix_data_base64: Some(BASE64_STANDARD.encode(data)),
                source_pump_swap_accounts: None,
                source_quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
                source_quote_amount_raw: Some(max_sol_cost),
            });
        }

        if starts_with(data, &BUY_EXACT_SOL_IN_DISCRIMINATOR)
            || starts_with(data, &BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR)
        {
            let spendable_sol = read_u64_le(data, 8)?;
            if spendable_sol == 0 {
                continue;
            }
            return Some(DetectedSwapLine {
                kind: "swap",
                source: "rust-sidecar",
                detected_at_ms: now_ms(),
                signature,
                wallet,
                input_mint: WRAPPED_SOL_MINT.to_string(),
                output_mint: mint,
                amount_raw: spendable_sol,
                is_native_input: true,
                counterparty_amount_raw: None,
                source_ix_accounts: Some(source_ix_accounts),
                source_ix_data_base64: Some(BASE64_STANDARD.encode(data)),
                source_pump_swap_accounts: None,
                source_quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
                source_quote_amount_raw: Some(spendable_sol),
            });
        }

        if starts_with(data, &SELL_DISCRIMINATOR) || starts_with(data, &SELL_V2_DISCRIMINATOR) {
            let token_amount = read_u64_le(data, 8)?;
            if token_amount == 0 {
                continue;
            }
            return Some(DetectedSwapLine {
                kind: "swap",
                source: "rust-sidecar",
                detected_at_ms: now_ms(),
                signature,
                wallet,
                input_mint: mint,
                output_mint: WRAPPED_SOL_MINT.to_string(),
                amount_raw: token_amount,
                is_native_input: false,
                counterparty_amount_raw: None,
                source_ix_accounts: None,
                source_ix_data_base64: None,
                source_pump_swap_accounts: None,
                source_quote_mint: None,
                source_quote_amount_raw: None,
            });
        }
    }

    None
}

fn extract_top_level_pumpswap_buy(
    ix: &yellowstone_grpc_proto::prelude::CompiledInstruction,
    account_keys: &[String],
    signature: &str,
    wallet: &str,
) -> Option<DetectedSwapLine> {
    let keys = ix
        .accounts
        .iter()
        .filter_map(|idx| account_keys.get(*idx as usize).cloned())
        .collect::<Vec<_>>();
    if keys.len() < 17 {
        return None;
    }
    if keys.get(1)? != wallet {
        return None;
    }
    let base_mint = keys.get(3)?.clone();
    let quote_mint = keys.get(4)?.clone();
    if quote_mint != WRAPPED_SOL_MINT && quote_mint != USDC_MINT {
        return None;
    }
    let data = ix.data.as_slice();
    let is_exact_quote_in = starts_with(data, &PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR);
    if !starts_with(data, &BUY_DISCRIMINATOR) && !is_exact_quote_in {
        return None;
    }
    let quote_in = read_u64_le(data, if is_exact_quote_in { 8 } else { 16 })?;
    let base_out = read_u64_le(data, if is_exact_quote_in { 16 } else { 8 })?;
    if quote_in == 0 {
        return None;
    }
    Some(DetectedSwapLine {
        kind: "swap",
        source: "rust-sidecar",
        detected_at_ms: now_ms(),
        signature: signature.to_string(),
        wallet: wallet.to_string(),
        input_mint: quote_mint.clone(),
        output_mint: base_mint,
        amount_raw: quote_in,
        is_native_input: quote_mint == WRAPPED_SOL_MINT,
        counterparty_amount_raw: if base_out > 0 { Some(base_out) } else { None },
        source_ix_accounts: None,
        source_ix_data_base64: None,
        source_pump_swap_accounts: Some(keys),
        source_quote_mint: Some(quote_mint),
        source_quote_amount_raw: Some(quote_in),
    })
}

fn starts_with(data: &[u8], disc: &[u8; 8]) -> bool {
    data.len() >= 8 && &data[..8] == disc
}

fn read_u64_le(data: &[u8], offset: usize) -> Option<u64> {
    let bytes = data.get(offset..offset + 8)?;
    Some(u64::from_le_bytes(bytes.try_into().ok()?))
}

fn now_ms() -> u128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0)
}
