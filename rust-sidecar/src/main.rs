use std::{
    collections::{HashMap, HashSet},
    env,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use futures::{stream, StreamExt};
use serde::Serialize;
#[cfg(feature = "jito-shredstream")]
use solana_entry::entry::Entry as SolanaEntry;
#[cfg(feature = "jito-shredstream")]
use solana_message::compiled_instruction::CompiledInstruction;
#[cfg(feature = "jito-shredstream")]
use solana_transaction::versioned::VersionedTransaction;
use tokio::time::sleep;
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient, subscribe_update::UpdateOneof, CommitmentLevel, SubscribeRequest,
    SubscribeRequestFilterTransactions,
};
use yellowstone_grpc_proto::tonic::{
    metadata::AsciiMetadataValue,
    transport::{Channel, ClientTlsConfig},
    Request, Status,
};

pub mod shredstream {
    tonic::include_proto!("shredstream");
}

mod executor;

const WRAPPED_SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const PUMP_FUN_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMPSWAP_PROGRAM_ID: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const BUY_DISCRIMINATOR: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR: [u8; 8] = [198, 46, 21, 82, 180, 217, 232, 112];
const BUY_EXACT_SOL_IN_DISCRIMINATOR: [u8; 8] = [56, 252, 116, 8, 158, 223, 205, 95];
const BUY_V2_DISCRIMINATOR: [u8; 8] = [184, 23, 238, 97, 103, 197, 211, 61];
const BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];
const SELL_DISCRIMINATOR: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const SELL_V2_DISCRIMINATOR: [u8; 8] = [93, 246, 130, 60, 231, 233, 64, 178];
const LAMPORTS_PER_SOL: f64 = 1_000_000_000.0;

#[derive(Debug, Clone)]
struct Config {
    yellowstone_enabled: bool,
    endpoint: Option<String>,
    x_token: Option<String>,
    jito_shredstream_enabled: bool,
    #[cfg_attr(not(feature = "jito-shredstream"), allow(dead_code))]
    jito_shredstream_grpc_url: String,
    tracked_wallets: Vec<String>,
    reconnect_ms: u64,
    hot_sniper: HotSniperConfig,
}

#[derive(Debug, Clone)]
struct HotSniperConfig {
    enabled: bool,
    buy_once_per_token: bool,
    bought_mints: Arc<Mutex<HashSet<String>>>,
    session_deployed_lamports: Arc<AtomicU64>,
    blockhash_cache: Arc<Mutex<Option<CachedBlockhash>>>,
    max_session_deployed_lamports: Option<u64>,
    min_buy_lamports: u64,
    max_buy_lamports: u64,
    max_sol_per_token_lamports: Option<u64>,
    copy_buy_percentage: f64,
    min_buy_behavior: String,
    slippage_bps: u64,
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
    blockhash_refresh_ms: u64,
    blockhash_max_age_ms: u64,
}

#[derive(Debug, Clone)]
struct CachedBlockhash {
    value: String,
    fetched_at_ms: u128,
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
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HotBuyLine {
    kind: &'static str,
    source: &'static str,
    detected_at_ms: u128,
    source_signature: String,
    wallet: String,
    mint: String,
    signature: String,
    source_amount_raw: u64,
    sol_in_lamports: u64,
    tokens_out_raw: u64,
    route: String,
    sender_path: Option<String>,
    elapsed_ms: u128,
    build_ms: Option<u128>,
    broadcast_ms: Option<u128>,
    tx_bytes: Option<usize>,
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let cfg = Config::from_env()?;
    if parse_env_bool("RUST_EXECUTOR_ENABLED", false) {
        executor::start_executor_stdin();
    }
    eprintln!(
        "rust-sidecar: starting yellowstone={} jito_shredstream={} hot_sniper={} wallets={}",
        cfg.yellowstone_enabled,
        cfg.jito_shredstream_enabled,
        cfg.hot_sniper.enabled,
        cfg.tracked_wallets.len()
    );
    if cfg.hot_sniper.enabled {
        eprintln!("rust-sidecar: hot sniper ON for Pump.fun source-replay buys");
        let hot = cfg.hot_sniper.clone();
        tokio::spawn(async move {
            run_hot_blockhash_refresher(hot).await;
        });
    }

    if cfg.jito_shredstream_enabled {
        #[cfg(feature = "jito-shredstream")]
        {
            let jito_cfg = cfg.clone();
            tokio::spawn(async move {
                loop {
                    if let Err(err) = run_jito_shredstream_once(&jito_cfg).await {
                        eprintln!(
                            "rust-sidecar: Jito ShredStream error: {err:#}; reconnecting in {}ms",
                            jito_cfg.reconnect_ms
                        );
                        sleep(Duration::from_millis(jito_cfg.reconnect_ms)).await;
                        continue;
                    }
                }
            });
        }
        #[cfg(not(feature = "jito-shredstream"))]
        {
            eprintln!(
                "rust-sidecar: JITO_SHREDSTREAM_ENABLED=true but this binary was built without --features jito-shredstream; skipping Jito ShredStream input."
            );
        }
    }

    if !cfg.yellowstone_enabled {
        futures::future::pending::<()>().await;
        return Ok(());
    }

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
            .ok();
        let x_token = env::var("SHYFT_GRPC_X_TOKEN")
            .or_else(|_| env::var("SHYFT_GRPC_TOKEN"))
            .ok();
        let jito_shredstream_enabled = parse_env_bool("JITO_SHREDSTREAM_ENABLED", false)
            || parse_env_bool("RUST_SIDECAR_JITO_SHREDSTREAM_ENABLED", false);
        let yellowstone_enabled = parse_env_bool("RUST_SIDECAR_YELLOWSTONE_ENABLED", true);
        if yellowstone_enabled && (endpoint.is_none() || x_token.is_none()) {
            bail!("Yellowstone sidecar mode is enabled but RUST_SIDECAR_GRPC_URL/SHYFT_GRPC_URL or SHYFT_GRPC_X_TOKEN is missing");
        }
        if !yellowstone_enabled && !jito_shredstream_enabled {
            bail!("Both Yellowstone and Jito ShredStream sidecar modes are disabled");
        }
        let jito_shredstream_grpc_url = env::var("JITO_SHREDSTREAM_GRPC_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:9999".to_string());
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
        let hot_sniper = HotSniperConfig::from_env();
        Ok(Self {
            yellowstone_enabled,
            endpoint,
            x_token,
            jito_shredstream_enabled,
            jito_shredstream_grpc_url,
            tracked_wallets,
            reconnect_ms,
            hot_sniper,
        })
    }
}

impl HotSniperConfig {
    fn from_env() -> Self {
        let enabled = parse_env_bool("RUST_HOT_SNIPER_ENABLED", false)
            && !parse_env_bool("SIMULATION_MODE", false)
            && !parse_env_bool("SHADOW_EXECUTION_MODE", false);
        let min_buy_lamports = sol_env_lamports("MIN_BUY_SOL", 0.01);
        let mut max_buy_lamports = sol_env_lamports("MAX_BUY_SOL", 1.0).max(1);
        let max_sol_per_token_lamports = optional_sol_env_lamports("MAX_SOL_PER_TOKEN");
        if let Some(max_per_token) = max_sol_per_token_lamports {
            max_buy_lamports = max_buy_lamports.min(max_per_token.max(1));
        }
        let static_tip = parse_env_u64("TIP_LAMPORTS", 65_000);
        let oracle_min_tip = parse_env_u64("JITO_TIP_ORACLE_MIN_LAMPORTS", static_tip);
        let oracle_max_tip = parse_env_optional_u64("JITO_TIP_ORACLE_MAX_LAMPORTS");
        let jito_tip_lamports = parse_env_optional_u64("RUST_HOT_SNIPER_JITO_TIP_LAMPORTS")
            .unwrap_or_else(|| {
                let uncapped = static_tip.max(oracle_min_tip);
                oracle_max_tip
                    .map(|cap| uncapped.min(cap))
                    .unwrap_or(uncapped)
            });
        let helius_tip_lamports = parse_env_optional_u64("RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS")
            .unwrap_or_else(|| parse_env_u64("HELIUS_TIP_LAMPORTS", static_tip));
        let priority_fee_micro_lamports =
            parse_env_optional_u64("RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS")
                .or_else(|| parse_env_optional_u64("DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS"))
                .unwrap_or_else(|| parse_env_u64("PRIORITY_FEE_MICRO_LAMPORTS", 50_000));
        let configured_regions = configured_jito_regions();
        Self {
            enabled,
            buy_once_per_token: parse_env_bool("BUY_ONCE_PER_TOKEN", true),
            bought_mints: Arc::new(Mutex::new(HashSet::new())),
            session_deployed_lamports: Arc::new(AtomicU64::new(0)),
            blockhash_cache: Arc::new(Mutex::new(None)),
            max_session_deployed_lamports: optional_sol_env_lamports("MAX_SESSION_DEPLOYED_SOL"),
            min_buy_lamports,
            max_buy_lamports,
            max_sol_per_token_lamports,
            copy_buy_percentage: parse_env_f64("COPY_BUY_PERCENTAGE", 100.0).clamp(0.0, 100.0),
            min_buy_behavior: env::var("MIN_BUY_BEHAVIOR")
                .unwrap_or_else(|_| "skip".to_string())
                .trim()
                .to_ascii_lowercase(),
            slippage_bps: parse_env_u64(
                "MIRROR_SLIPPAGE_BPS",
                parse_env_u64("SWAP_SLIPPAGE_BPS", 500),
            ),
            compute_unit_limit: parse_env_u64("COMPUTE_UNIT_LIMIT", 160_000).min(1_400_000) as u32,
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
            use_jito_direct: parse_env_bool("USE_JITO_DIRECT", false),
            use_jito_fallback: parse_env_bool("USE_JITO_FALLBACK", true),
            sender_rpc_fallback_enabled: parse_env_bool("SENDER_RPC_FALLBACK_ENABLED", true),
            execution_multi_send_enabled: parse_env_bool("EXECUTION_MULTI_SEND_ENABLED", false)
                || parse_env_bool("BROADCAST_RACE_ENABLED", false),
            jito_send_mode: env::var("JITO_SEND_MODE").unwrap_or_else(|_| "bundle".to_string()),
            jito_transaction_urls: configured_regions
                .iter()
                .map(|region| format!("{}/api/v1/transactions", jito_region_base_url(region)))
                .collect(),
            jito_bundle_urls: configured_regions
                .iter()
                .map(|region| format!("{}/api/v1/bundles", jito_region_base_url(region)))
                .collect(),
            sender_endpoint_url: env::var("SENDER_ENDPOINT")
                .unwrap_or_else(|_| "http://ewr-sender.helius-rpc.com/fast".to_string()),
            rpc_fallback_url: read_rpc_url(),
            jito_auth_uuid: env::var("JITO_AUTH_UUID")
                .ok()
                .filter(|v| !v.trim().is_empty()),
            blockhash_refresh_ms: parse_env_u64("RUST_HOT_SNIPER_BLOCKHASH_REFRESH_MS", 1000)
                .max(250),
            blockhash_max_age_ms: parse_env_u64("RUST_HOT_SNIPER_BLOCKHASH_MAX_AGE_MS", 15_000)
                .max(1000),
        }
    }
}

fn parse_env_bool(name: &str, fallback: bool) -> bool {
    match env::var(name) {
        Ok(value) => match value.trim().to_ascii_lowercase().as_str() {
            "true" | "1" | "yes" | "on" => true,
            "false" | "0" | "no" | "off" => false,
            _ => fallback,
        },
        Err(_) => fallback,
    }
}

fn parse_env_u64(name: &str, fallback: u64) -> u64 {
    env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<u64>().ok())
        .unwrap_or(fallback)
}

fn parse_env_optional_u64(name: &str) -> Option<u64> {
    env::var(name)
        .ok()
        .and_then(|v| {
            let trimmed = v.trim();
            if trimmed.is_empty() {
                None
            } else {
                trimmed.parse::<u64>().ok()
            }
        })
        .filter(|v| *v > 0)
}

fn parse_env_f64(name: &str, fallback: f64) -> f64 {
    env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<f64>().ok())
        .filter(|v| v.is_finite())
        .unwrap_or(fallback)
}

fn sol_env_lamports(name: &str, fallback_sol: f64) -> u64 {
    (parse_env_f64(name, fallback_sol).max(0.0) * LAMPORTS_PER_SOL).round() as u64
}

fn optional_sol_env_lamports(name: &str) -> Option<u64> {
    env::var(name)
        .ok()
        .and_then(|v| {
            let trimmed = v.trim();
            if trimmed.is_empty() {
                None
            } else {
                trimmed.parse::<f64>().ok()
            }
        })
        .filter(|v| v.is_finite() && *v > 0.0)
        .map(|v| (v * LAMPORTS_PER_SOL).round() as u64)
}

fn configured_jito_regions() -> Vec<String> {
    let raw = env::var("JITO_REGIONS").unwrap_or_default();
    let mut seen = HashSet::new();
    let mut regions = raw
        .split(',')
        .map(|s| s.trim().to_ascii_lowercase())
        .filter(|s| !s.is_empty())
        .filter(|s| seen.insert(s.clone()))
        .collect::<Vec<_>>();
    if regions.is_empty() {
        let region = env::var("JITO_REGION")
            .unwrap_or_default()
            .trim()
            .to_ascii_lowercase();
        regions.push(region);
    }
    regions
}

fn jito_region_base_url(region: &str) -> &'static str {
    match region {
        "ny" | "new-york" | "newyork" | "ewr" | "va" | "virginia" | "ashburn" => {
            "https://ny.mainnet.block-engine.jito.wtf"
        }
        "amsterdam" | "ams" => "https://amsterdam.mainnet.block-engine.jito.wtf",
        "dublin" => "https://dublin.mainnet.block-engine.jito.wtf",
        "frankfurt" | "fra" => "https://frankfurt.mainnet.block-engine.jito.wtf",
        "london" | "lon" => "https://london.mainnet.block-engine.jito.wtf",
        "singapore" | "sg" | "sgp" => "https://singapore.mainnet.block-engine.jito.wtf",
        "tokyo" | "tyo" => "https://tokyo.mainnet.block-engine.jito.wtf",
        "slc" | "salt-lake-city" => "https://slc.mainnet.block-engine.jito.wtf",
        _ => "https://mainnet.block-engine.jito.wtf",
    }
}

fn read_rpc_url() -> String {
    if let Ok(url) = env::var("SECURE_RPC_URL") {
        if !url.trim().is_empty() {
            return url;
        }
    }
    if let Ok(key) = env::var("HELIUS_API_KEY") {
        if !key.trim().is_empty() {
            return format!("https://mainnet.helius-rpc.com/?api-key={key}");
        }
    }
    "https://mainnet.helius-rpc.com".to_string()
}

async fn run_hot_blockhash_refresher(hot: HotSniperConfig) {
    loop {
        match executor::fetch_latest_blockhash(&hot.rpc_fallback_url).await {
            Ok(blockhash) => store_hot_blockhash(&hot, blockhash),
            Err(err) => eprintln!("rust-sidecar: hot blockhash refresh failed: {err:#}"),
        }
        sleep(Duration::from_millis(hot.blockhash_refresh_ms)).await;
    }
}

fn store_hot_blockhash(hot: &HotSniperConfig, value: String) {
    if let Ok(mut cache) = hot.blockhash_cache.lock() {
        *cache = Some(CachedBlockhash {
            value,
            fetched_at_ms: now_ms(),
        });
    }
}

fn cached_hot_blockhash(hot: &HotSniperConfig) -> Option<String> {
    let cache = hot.blockhash_cache.lock().ok()?;
    let cached = cache.as_ref()?;
    if now_ms().saturating_sub(cached.fetched_at_ms) <= hot.blockhash_max_age_ms as u128 {
        return Some(cached.value.clone());
    }
    None
}

async fn hot_blockhash(hot: &HotSniperConfig) -> Result<String> {
    if let Some(blockhash) = cached_hot_blockhash(hot) {
        return Ok(blockhash);
    }
    let blockhash = executor::fetch_latest_blockhash(&hot.rpc_fallback_url).await?;
    store_hot_blockhash(hot, blockhash.clone());
    Ok(blockhash)
}

async fn run_once(cfg: &Config) -> Result<()> {
    let token: AsciiMetadataValue = cfg
        .x_token
        .as_ref()
        .context("x-token missing")?
        .parse()
        .context("failed to parse SHYFT_GRPC_X_TOKEN as metadata")?;
    let channel = Channel::from_shared(cfg.endpoint.clone().context("endpoint missing")?)?
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
        if let Some(line) = extract_swap(
            &info,
            &cfg.tracked_wallets,
            Some(tx_update.slot),
            Some(info.index),
        ) {
            emit_detected_line(cfg, line).await?;
        }
    }

    Ok(())
}

#[cfg(feature = "jito-shredstream")]
async fn run_jito_shredstream_once(cfg: &Config) -> Result<()> {
    use shredstream::{shredstream_proxy_client::ShredstreamProxyClient, SubscribeEntriesRequest};

    let mut client = ShredstreamProxyClient::connect(cfg.jito_shredstream_grpc_url.clone()).await?;
    let mut stream = client
        .subscribe_entries(SubscribeEntriesRequest {})
        .await?
        .into_inner();
    eprintln!(
        "rust-sidecar: subscribed to Jito ShredStream entries via {}",
        cfg.jito_shredstream_grpc_url
    );

    while let Some(slot_entry) = stream.message().await? {
        let entries = match bincode::deserialize::<Vec<SolanaEntry>>(&slot_entry.entries) {
            Ok(entries) => entries,
            Err(err) => {
                eprintln!(
                    "rust-sidecar: Jito ShredStream slot {} entry deserialize failed: {err}",
                    slot_entry.slot
                );
                continue;
            }
        };
        let mut tx_index = 0_u64;
        for entry in entries {
            for tx in entry.transactions {
                if let Some(line) =
                    extract_shred_swap(&tx, &cfg.tracked_wallets, slot_entry.slot, tx_index)
                {
                    emit_detected_line(cfg, line).await?;
                }
                tx_index = tx_index.saturating_add(1);
            }
        }
    }
    Ok(())
}

async fn emit_detected_line(cfg: &Config, line: DetectedSwapLine) -> Result<()> {
    match try_hot_sniper_buy(cfg, &line).await {
        HotDecision::Bought(hot_line) => {
            println!("{}", serde_json::to_string(&hot_line)?);
        }
        HotDecision::Fallback(reason) => {
            if let Some(reason) = reason {
                eprintln!(
                    "rust-sidecar: hot sniper fallback for {}...: {reason}",
                    line.output_mint.chars().take(8).collect::<String>()
                );
            }
            println!("{}", serde_json::to_string(&line)?);
        }
    }
    Ok(())
}

enum HotDecision {
    Bought(HotBuyLine),
    Fallback(Option<String>),
}

async fn try_hot_sniper_buy(cfg: &Config, line: &DetectedSwapLine) -> HotDecision {
    let hot = &cfg.hot_sniper;
    if !hot.enabled {
        return HotDecision::Fallback(None);
    }
    if line.input_mint != WRAPPED_SOL_MINT || !line.is_native_input {
        return HotDecision::Fallback(None);
    }
    let Some(source_ix_accounts) = line.source_ix_accounts.clone() else {
        return HotDecision::Fallback(None);
    };
    let Some(source_ix_data_base64) = line.source_ix_data_base64.clone() else {
        return HotDecision::Fallback(None);
    };
    let Some(sol_in_lamports) = hot_sized_lamports(hot, line.amount_raw) else {
        return HotDecision::Fallback(Some("copy-sized amount below minimum".to_string()));
    };
    if sol_in_lamports == 0 {
        return HotDecision::Fallback(Some("copy-sized amount is zero".to_string()));
    }

    if hot.buy_once_per_token {
        let mut bought = match hot.bought_mints.lock() {
            Ok(guard) => guard,
            Err(_) => {
                return HotDecision::Fallback(Some("buy-once state lock poisoned".to_string()))
            }
        };
        if bought.contains(&line.output_mint) {
            return HotDecision::Fallback(Some(
                "mint already hot-bought by Rust this session".to_string(),
            ));
        }
        bought.insert(line.output_mint.clone());
    }

    let session_reserved = reserve_hot_session_lamports(hot, sol_in_lamports);
    if !session_reserved {
        if hot.buy_once_per_token {
            if let Ok(mut bought) = hot.bought_mints.lock() {
                bought.remove(&line.output_mint);
            }
        }
        return HotDecision::Fallback(Some(
            "session deployed SOL cap would be exceeded".to_string(),
        ));
    }

    let started = Instant::now();
    let blockhash = match hot_blockhash(hot).await {
        Ok(blockhash) => blockhash,
        Err(err) => {
            release_hot_reservations(hot, &line.output_mint, sol_in_lamports);
            return HotDecision::Fallback(Some(format!("blockhash fetch failed: {err}")));
        }
    };

    let command = executor::ExecutePumpfunBuyCommand {
        id: format!(
            "rust-hot-{}-{}",
            now_ms(),
            line.signature.chars().take(8).collect::<String>()
        ),
        mode: "source-replay".to_string(),
        route: "pumpfun-hot-source-replay-buy".to_string(),
        mint: line.output_mint.clone(),
        sol_in_lamports,
        tokens_out_raw: None,
        slippage_bps: hot.slippage_bps,
        exact_sol_in: false,
        source_ix_accounts: Some(source_ix_accounts),
        source_ix_data_base64: Some(source_ix_data_base64),
        bonding_curve: None,
        creator: None,
        token_program_id: None,
        blockhash,
        compute_unit_limit: hot.compute_unit_limit,
        priority_fee_micro_lamports: hot.priority_fee_micro_lamports,
        jito_tip_lamports: hot.jito_tip_lamports,
        helius_tip_lamports: hot.helius_tip_lamports,
        use_jito_direct: hot.use_jito_direct,
        use_jito_fallback: hot.use_jito_fallback,
        sender_rpc_fallback_enabled: hot.sender_rpc_fallback_enabled,
        execution_multi_send_enabled: hot.execution_multi_send_enabled,
        jito_send_mode: hot.jito_send_mode.clone(),
        jito_transaction_urls: hot.jito_transaction_urls.clone(),
        jito_bundle_urls: hot.jito_bundle_urls.clone(),
        sender_endpoint_url: hot.sender_endpoint_url.clone(),
        rpc_fallback_url: hot.rpc_fallback_url.clone(),
        jito_auth_uuid: hot.jito_auth_uuid.clone(),
    };

    match executor::execute_pumpfun_buy_command(command, started).await {
        Ok(response) if response.ok => {
            let signature = response.signature.unwrap_or_default();
            if signature.is_empty() {
                release_hot_reservations(hot, &line.output_mint, sol_in_lamports);
                return HotDecision::Fallback(Some(
                    "Rust executor returned no signature".to_string(),
                ));
            }
            HotDecision::Bought(HotBuyLine {
                kind: "hotBuy",
                source: "rust-hot-sniper",
                detected_at_ms: now_ms(),
                source_signature: line.signature.clone(),
                wallet: line.wallet.clone(),
                mint: line.output_mint.clone(),
                signature,
                source_amount_raw: line.amount_raw,
                sol_in_lamports: response.sol_in_lamports.unwrap_or(sol_in_lamports),
                tokens_out_raw: response.tokens_out_raw.unwrap_or(1).max(1),
                route: response
                    .route
                    .unwrap_or_else(|| "pumpfun-hot-source-replay-buy".to_string()),
                sender_path: response.sender_path,
                elapsed_ms: response.elapsed_ms,
                build_ms: response.build_ms,
                broadcast_ms: response.broadcast_ms,
                tx_bytes: response.tx_bytes,
                source_slot: line.source_slot,
                source_transaction_index: line.source_transaction_index,
            })
        }
        Ok(response) => {
            release_hot_reservations(hot, &line.output_mint, sol_in_lamports);
            HotDecision::Fallback(Some(
                response
                    .error
                    .unwrap_or_else(|| "Rust executor failed".to_string()),
            ))
        }
        Err(err) => {
            release_hot_reservations(hot, &line.output_mint, sol_in_lamports);
            HotDecision::Fallback(Some(format!("{err:#}")))
        }
    }
}

fn hot_sized_lamports(hot: &HotSniperConfig, source_lamports: u64) -> Option<u64> {
    let copied = ((source_lamports as f64) * (hot.copy_buy_percentage / 100.0)).floor() as u64;
    let mut sized = copied;
    if sized < hot.min_buy_lamports {
        match hot.min_buy_behavior.as_str() {
            "floor" | "min" => sized = hot.min_buy_lamports,
            "allow" | "copy" | "copy-sized" => {}
            _ => return None,
        }
    }
    sized = sized.min(hot.max_buy_lamports);
    if let Some(max_per_token) = hot.max_sol_per_token_lamports {
        sized = sized.min(max_per_token);
    }
    Some(sized.max(1))
}

fn reserve_hot_session_lamports(hot: &HotSniperConfig, lamports: u64) -> bool {
    let Some(cap) = hot.max_session_deployed_lamports else {
        return true;
    };
    loop {
        let current = hot.session_deployed_lamports.load(Ordering::Relaxed);
        let Some(next) = current.checked_add(lamports) else {
            return false;
        };
        if next > cap {
            return false;
        }
        if hot
            .session_deployed_lamports
            .compare_exchange(current, next, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok()
        {
            return true;
        }
    }
}

fn release_hot_reservations(hot: &HotSniperConfig, mint: &str, lamports: u64) {
    if hot.buy_once_per_token {
        if let Ok(mut bought) = hot.bought_mints.lock() {
            bought.remove(mint);
        }
    }
    if hot.max_session_deployed_lamports.is_some() {
        hot.session_deployed_lamports
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
                Some(current.saturating_sub(lamports))
            })
            .ok();
    }
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
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
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
            if let Some(line) = extract_top_level_pumpswap_buy(
                ix,
                &account_keys,
                &signature,
                &wallet,
                source_slot,
                source_transaction_index,
            ) {
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
            && source_ix_accounts
                .get(11)
                .is_some_and(|v| v == PUMP_FUN_PROGRAM_ID);
        let is_v2 = source_ix_accounts.len() >= 27
            && source_ix_accounts.get(1).is_some()
            && source_ix_accounts
                .get(26)
                .is_some_and(|v| v == PUMP_FUN_PROGRAM_ID);
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
                source_slot,
                source_transaction_index,
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
                source_slot,
                source_transaction_index,
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
                source_slot,
                source_transaction_index,
            });
        }
    }

    None
}

#[cfg(feature = "jito-shredstream")]
fn extract_shred_swap(
    tx: &VersionedTransaction,
    tracked_wallets: &[String],
    source_slot: u64,
    source_transaction_index: u64,
) -> Option<DetectedSwapLine> {
    let source_slot = Some(source_slot);
    let source_transaction_index = Some(source_transaction_index);
    let account_keys = tx
        .message
        .static_account_keys()
        .iter()
        .map(|key| key.to_string())
        .collect::<Vec<_>>();
    let wallet = tracked_wallets
        .iter()
        .find(|wallet| account_keys.iter().any(|key| key == *wallet))?
        .clone();
    let signature = tx.signatures.first()?.to_string();

    for ix in tx.message.instructions() {
        let program_id = account_keys.get(ix.program_id_index as usize)?;
        if program_id == PUMPSWAP_PROGRAM_ID {
            if let Some(line) = extract_top_level_pumpswap_buy_from_compiled(
                ix,
                &account_keys,
                &signature,
                &wallet,
                "jito-shredstream",
                source_slot,
                source_transaction_index,
            ) {
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
            && source_ix_accounts.get(6).is_some_and(|v| v == &wallet)
            && source_ix_accounts
                .get(11)
                .is_some_and(|v| v == PUMP_FUN_PROGRAM_ID);
        let is_v2 = source_ix_accounts.len() >= 27
            && source_ix_accounts.get(1).is_some()
            && source_ix_accounts.get(13).is_some_and(|v| v == &wallet)
            && source_ix_accounts
                .get(26)
                .is_some_and(|v| v == PUMP_FUN_PROGRAM_ID);
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
                source: "jito-shredstream",
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
                source_slot,
                source_transaction_index,
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
                source: "jito-shredstream",
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
                source_slot,
                source_transaction_index,
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
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
) -> Option<DetectedSwapLine> {
    let keys = ix
        .accounts
        .iter()
        .filter_map(|idx| account_keys.get(*idx as usize).cloned())
        .collect::<Vec<_>>();
    extract_top_level_pumpswap_buy_from_parts(
        &keys,
        ix.data.as_slice(),
        signature,
        wallet,
        "rust-sidecar",
        source_slot,
        source_transaction_index,
    )
}

#[cfg(feature = "jito-shredstream")]
fn extract_top_level_pumpswap_buy_from_compiled(
    ix: &CompiledInstruction,
    account_keys: &[String],
    signature: &str,
    wallet: &str,
    source: &'static str,
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
) -> Option<DetectedSwapLine> {
    let keys = ix
        .accounts
        .iter()
        .filter_map(|idx| account_keys.get(*idx as usize).cloned())
        .collect::<Vec<_>>();
    extract_top_level_pumpswap_buy_from_parts(
        &keys,
        ix.data.as_slice(),
        signature,
        wallet,
        source,
        source_slot,
        source_transaction_index,
    )
}

fn extract_top_level_pumpswap_buy_from_parts(
    keys: &[String],
    data: &[u8],
    signature: &str,
    wallet: &str,
    source: &'static str,
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
) -> Option<DetectedSwapLine> {
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
        source,
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
        source_pump_swap_accounts: Some(keys.to_vec()),
        source_quote_mint: Some(quote_mint),
        source_quote_amount_raw: Some(quote_in),
        source_slot,
        source_transaction_index,
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
