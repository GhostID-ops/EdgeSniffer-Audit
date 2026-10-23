#![recursion_limit = "256"]

use std::{
    collections::{HashMap, HashSet},
    env,
    fs::{self, OpenOptions},
    io::{BufRead, Read, Seek, SeekFrom, Write},
    path::Path,
    str::FromStr,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex, OnceLock,
    },
    time::{Duration, Instant},
};

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as BASE64_STANDARD, Engine as _};
use futures::{channel::mpsc as futures_mpsc, stream, StreamExt};
use pumpfun_sidecar::{
    parity_core::{MayhemEvidence, MayhemStatus, ProviderSource},
    teacher_impulse_emitter::{ObservationSide, TeacherImpulseObservation},
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
#[cfg(feature = "jito-shredstream")]
use solana_entry::entry::Entry as SolanaEntry;
use solana_keypair::Keypair;
#[cfg(feature = "jito-shredstream")]
use solana_message::compiled_instruction::CompiledInstruction;
use solana_pubkey::Pubkey;
use solana_signer::Signer;
#[cfg(feature = "jito-shredstream")]
use solana_transaction::versioned::VersionedTransaction;
use tokio::time::{sleep, timeout};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    net::{TcpListener, TcpStream},
    sync::{
        mpsc::{self, UnboundedSender},
        Notify,
    },
};
use yellowstone_grpc_proto::geyser::{
    geyser_client::GeyserClient, subscribe_request_filter_accounts_filter,
    subscribe_request_filter_accounts_filter_memcmp, subscribe_update::UpdateOneof,
    CommitmentLevel, SubscribeRequest, SubscribeRequestAccountsDataSlice,
    SubscribeRequestFilterAccounts, SubscribeRequestFilterAccountsFilter,
    SubscribeRequestFilterAccountsFilterMemcmp, SubscribeRequestFilterTransactions,
    SubscribeRequestPing, SubscribeUpdate, TokenAccountExpansionControlFlag,
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
mod exit_watcher;

const WRAPPED_SOL_MINT: &str = "So11111111111111111111111111111111111111112";
const USDC_MINT: &str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const PUMP_FUN_PROGRAM_ID: &str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P";
const PUMPSWAP_PROGRAM_ID: &str = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA";
const TOKEN_PROGRAM_ID: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const TOKEN_2022_PROGRAM_ID: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
#[cfg(test)]
const ASSOCIATED_TOKEN_PROGRAM_ID: &str = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL";
#[cfg(test)]
const PUMP_FUN_FEE_PROGRAM_ID: &str = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ";
const BUY_DISCRIMINATOR: [u8; 8] = [102, 6, 61, 18, 1, 218, 235, 234];
const PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR: [u8; 8] = [198, 46, 21, 82, 180, 217, 232, 112];
const BUY_EXACT_SOL_IN_DISCRIMINATOR: [u8; 8] = [56, 252, 116, 8, 158, 223, 205, 95];
const BUY_V2_DISCRIMINATOR: [u8; 8] = [184, 23, 238, 97, 103, 197, 211, 61];
const BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR: [u8; 8] = [194, 171, 28, 70, 104, 77, 91, 47];
const SELL_DISCRIMINATOR: [u8; 8] = [51, 230, 133, 164, 1, 127, 131, 173];
const SELL_V2_DISCRIMINATOR: [u8; 8] = [93, 246, 130, 60, 231, 233, 64, 178];
const CREATE_DISCRIMINATOR: [u8; 8] = [24, 30, 200, 40, 5, 28, 7, 119];
const CREATE_V2_DISCRIMINATOR: [u8; 8] = [214, 144, 76, 236, 95, 139, 49, 180];
const PUMP_FUN_TRADE_FEE_BPS: u128 = 125;
const PUMP_FUN_SELL_TRADE_FEE_BPS: u128 = 125;
const LAMPORTS_PER_SOL: f64 = 1_000_000_000.0;
const PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES: u64 = 30_000_000_000;
const PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES: u64 = 1_073_000_000_000_000;
const PUMPFUN_TOKEN_TOTAL_SUPPLY: u64 = 1_000_000_000_000_000;

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
    exit_watcher_enabled: bool,
    exit_subscription_refresh_ms: u64,
    exit_poll_fallback_enabled: bool,
    exit_poll_fallback_ms: u64,
    exit_poll_rpc_timeout_ms: u64,
    hot_sniper: HotSniperConfig,
    selected_candidate_sniper: SelectedCandidateSniperConfig,
    selected_candidate_curve_cache: CandidateCurveCache,
    selected_candidate_shred_curve_replay: SelectedCandidateShredCurveReplay,
    native_strict_selector: NativeStrictSelectorConfig,
}

#[derive(Debug, Clone)]
struct HotSniperConfig {
    enabled: bool,
    buy_once_per_token: bool,
    bought_mints: Arc<Mutex<HashSet<String>>>,
    /// Source signatures already mirrored this session. Always enforced (independent of
    /// `buy_once_per_token`) so a single source tx surfaced multiple times by ShredStream
    /// only ever produces one real buy.
    bought_signatures: Arc<Mutex<HashSet<String>>>,
    session_deployed_lamports: Arc<AtomicU64>,
    blockhash_cache: Arc<Mutex<Option<CachedBlockhash>>>,
    max_session_deployed_lamports: Option<u64>,
    min_market_cap_usd: Option<f64>,
    max_market_cap_usd: Option<f64>,
    min_market_cap_sol: Option<f64>,
    max_market_cap_sol: Option<f64>,
    sol_price_usd: Option<f64>,
    market_cap_filter_enabled: bool,
    market_cap_filter_fail_open: bool,
    market_cap_rpc_timeout_ms: u64,
    min_buy_lamports: u64,
    max_buy_lamports: u64,
    max_sol_per_token_lamports: Option<u64>,
    copy_buy_percentage: f64,
    min_buy_behavior: String,
    slippage_bps: u64,
    exact_sol_in: bool,
    compute_unit_limit: u32,
    priority_fee_micro_lamports: u64,
    jito_tip_lamports: u64,
    helius_tip_lamports: u64,
    max_execution_fee_lamports: Option<u64>,
    max_execution_fee_pct_of_buy: Option<f64>,
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
struct SelectedCandidateSniperConfig {
    enabled: bool,
    submit_enabled: bool,
    no_submit_dry_build_enabled: bool,
    bind_host: String,
    bind_port: u16,
    required_lane: Option<String>,
    allowed_lanes: Vec<String>,
    required_policy: Option<String>,
    max_event_lag_ms: u64,
    max_session_buys: u64,
    max_open_positions: u64,
    buy_lamports: u64,
    min_entry_mc_usd: Option<f64>,
    max_entry_mc_usd: Option<f64>,
    max_entry_drift_bps: u64,
    selected_event_fallback_max_entry_drift_bps: u64,
    landing_buffer_bps: u64,
    selected_event_fallback_landing_buffer_bps: u64,
    fee_quote_haircut_bps: u64,
    max_min_out_below_protected_bps: u64,
    buy_output_policy: String,
    max_entry_market_cap_usd: Option<f64>,
    post_buy_reject_fill_below_protected_min: bool,
    reject_mayhem_retry_promoted: bool,
    reject_weak_mayhem_recovered: bool,
    audit_file: Option<String>,
    candidate_source: Option<String>,
    candidate_start_at_end: bool,
    candidate_poll_ms: u64,
    candidate_offset: Arc<AtomicU64>,
    curve_cache_enabled: bool,
    curve_cache_owner_wide: bool,
    curve_cache_require_independent: bool,
    curve_cache_wait_ms: u64,
    curve_cache_max_age_ms: u64,
    reserve_observation_source: Option<String>,
    reserve_observation_start_at_end: bool,
    reserve_observation_poll_ms: u64,
    min_send_time_mc_drift_pct: Option<f64>,
    max_send_time_mc_drift_pct: Option<f64>,
    assumed_shadow_exit_roi_pct: Option<f64>,
    min_live_entry_adjusted_roi_pct: Option<f64>,
    curve_rpc_fallback_enabled: bool,
    optimistic_exit_activate_enabled: bool,
    early_balance_activate_enabled: bool,
    early_balance_timeout_ms: u64,
    early_balance_rpc_fallback_enabled: bool,
    exact_balance_proof_enabled: bool,
    exact_balance_proof_timeout_ms: u64,
    exact_balance_proof_poll_ms: u64,
    exact_balance_proof_rpc_timeout_ms: u64,
    direct_ata_balance_probe_enabled: bool,
    direct_ata_balance_probe_per_candidate_enabled: bool,
    allow_pre_proof_sell_broadcast: bool,
    pre_proof_sell_require_local_landed: bool,
    landed_safe_exit_enabled: bool,
    landed_safe_exit_timeout_ms: u64,
    landed_safe_exit_poll_ms: u64,
    landed_safe_exit_history_fallback_enabled: bool,
    landed_safe_exit_rpc_fallback_enabled: bool,
    guaranteed_min_out_exit_enabled: bool,
    guaranteed_min_out_exit_bps: u64,
    balance_cache_prebuy_watch_settle_ms: u64,
    balance_cache_enabled: bool,
    balance_cache_wallet_owner_stream_enabled: bool,
    balance_cache_wait_ms: u64,
    balance_cache_max_age_ms: u64,
    balance_cache_rpc_fallback_enabled: bool,
    balance_cache: SelectedCandidateBalanceCache,
    wallet_reconcile_enabled: bool,
    wallet_reconcile_auto_flatten_enabled: bool,
    wallet_reconcile_interval_ms: u64,
    wallet_reconcile_unmanaged_grace_ms: u64,
    wallet_reconcile_seen: Arc<Mutex<HashMap<String, u64>>>,
    pending_exact_proof_zero_since: Arc<Mutex<HashMap<String, u64>>>,
    forwarded_keys: Arc<Mutex<HashSet<String>>>,
    active_mints: Arc<Mutex<HashSet<String>>>,
    session_attempts: Arc<AtomicU64>,
}

#[derive(Debug, Clone)]
struct CandidateCurveCache {
    watched: Arc<Mutex<HashSet<String>>>,
    states: Arc<Mutex<HashMap<String, CachedCurveAccount>>>,
    yellowstone_states: Arc<Mutex<HashMap<String, CachedCurveAccount>>>,
    max_states: usize,
    prune_every_updates: u64,
    update_count: Arc<AtomicU64>,
    account_update_count: Arc<AtomicU64>,
    decode_success_count: Arc<AtomicU64>,
    decode_skip_count: Arc<AtomicU64>,
    yellowstone_account_update_count: Arc<AtomicU64>,
    yellowstone_decode_success_count: Arc<AtomicU64>,
    yellowstone_decode_skip_count: Arc<AtomicU64>,
}

#[derive(Debug, Clone, Default)]
struct SelectedCandidateShredCurveReplay {
    states: Arc<Mutex<HashMap<String, ShredReplayCurveState>>>,
}

#[derive(Debug, Clone)]
struct ShredReplayCurveState {
    state: CurveState,
    seed_slot: u64,
    seed_observed_at_ms: u64,
    seed_source: &'static str,
    last_slot: u64,
    last_transaction_index: u64,
    last_instruction_index: u32,
    updated_at_ms: u64,
}

#[derive(Debug, Clone, Default)]
struct SelectedCandidateBalanceCache {
    watched: Arc<Mutex<HashMap<String, WatchedTokenAccount>>>,
    balances: Arc<Mutex<HashMap<String, CachedTokenAccountBalance>>>,
    balances_by_mint: Arc<Mutex<HashMap<String, CachedTokenAccountBalance>>>,
    first_nonzero_exact_by_ata: Arc<Mutex<HashMap<String, CachedTokenAccountBalance>>>,
    first_nonzero_exact_by_mint: Arc<Mutex<HashMap<String, CachedTokenAccountBalance>>>,
    submitted_buy_proofs: Arc<Mutex<HashMap<String, SubmittedBuyTokenProof>>>,
    recent_shredstream_signatures: Arc<Mutex<HashMap<String, (u64, u64)>>>,
    notify: Arc<Notify>,
}

#[derive(Debug, Clone)]
struct WatchedTokenAccount {
    mint: String,
    token_program_id: String,
    watched_at_ms: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct WatchedTokenAccountSubscription {
    ata: String,
    mint: String,
    token_program_id: String,
}

#[derive(Debug, Clone)]
struct CachedTokenAccountBalance {
    ata: String,
    mint: String,
    owner: String,
    token_program_id: String,
    amount_raw: u64,
    slot: u64,
    observed_at_ms: u64,
    exact: bool,
    source: &'static str,
}

#[derive(Debug, Clone)]
struct SubmittedBuyTokenProof {
    ata: String,
    mint: String,
    owner: String,
    token_program_id: String,
    amount_raw: u64,
    observed_at_ms: u64,
}

fn selected_candidate_signature_only_balance_proof_enabled() -> bool {
    parse_env_bool(
        "RUST_SELECTED_CANDIDATE_SIGNATURE_ONLY_BALANCE_PROOF_ENABLED",
        false,
    ) && parse_env_bool(
        "RUST_SELECTED_CANDIDATE_UNSAFE_SIGNATURE_ONLY_BALANCE_PROOF_ENABLED",
        false,
    )
}

#[derive(Debug, Clone)]
struct WalletTokenPosition {
    mint: String,
    token_program_id: String,
    amount_raw: u64,
    ui_amount: Option<String>,
}

#[derive(Debug, Clone)]
struct NativeStrictSelectorConfig {
    enabled: bool,
    state: Arc<Mutex<NativeStrictState>>,
    parity_candidates: Arc<Mutex<HashMap<String, NativeStrictParityCandidate>>>,
    parity_candidate_source: Option<String>,
    comparison_candidate_source: Option<String>,
    parity_candidate_start_at_end: bool,
    parity_max_age_ms: u64,
    parity_wait_ms: u64,
    feature_sol_price_usd: f64,
    min_feature_mc_usd: Option<f64>,
    max_feature_mc_usd: Option<f64>,
    source_template_cache_miss_fallback: bool,
    max_mints: usize,
    mint_ttl_ms: u64,
    status_interval_ms: u64,
}

#[derive(Debug, Clone)]
struct NativeStrictParityCandidate {
    observed_at_ms: u64,
    ingested_at_ms: u64,
    source_signature: Option<String>,
    feature_market_cap_usd: f64,
}

#[derive(Debug, Default)]
struct NativeStrictState {
    mints: HashMap<String, NativeMintState>,
    accepted_keys: HashSet<String>,
    accepted_mints: HashSet<String>,
    counters: NativeStrictCounters,
    last_status_at_ms: u64,
}

#[derive(Debug, Default, Clone, Serialize)]
struct NativeStrictCounters {
    observations: u64,
    creations: u64,
    token2022_creations: u64,
    flows: u64,
    buys: u64,
    sells: u64,
    token2022_flows: u64,
    pre_gate_passes: u64,
    curve_checks: u64,
    selected: u64,
    rejects: HashMap<String, u64>,
}

#[derive(Debug, Default)]
struct NativeMintState {
    creation: Option<NativeCreationState>,
    flows: Vec<NativeFlowEvent>,
    sequence: u64,
    last_observed_at_ms: u64,
}

#[derive(Debug, Clone)]
struct NativeCreationState {
    signature: String,
    observed_at_ms: u64,
    slot: u64,
    token_program_id: String,
    bonding_curve: Option<String>,
}

#[derive(Debug, Clone)]
struct NativeFlowEvent {
    signature: String,
    wallet: String,
    side: NativeFlowSide,
    quote_lamports: Option<u64>,
    sell_quote_lamports: Option<u64>,
    sell_token_amount_raw: Option<u64>,
    slot: u64,
    transaction_index: u64,
    instruction_index: u32,
    observed_at_ms: u64,
    mint: String,
    bonding_curve: String,
    token_program_id: String,
    source_ix_accounts: Vec<String>,
    source_ix_data_base64: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum NativeFlowSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone)]
enum NativePumpfunObservation {
    Creation {
        mint: String,
        signature: String,
        slot: u64,
        transaction_index: u64,
        observed_at_ms: u64,
        token_program_id: String,
        bonding_curve: Option<String>,
    },
    Flow(NativeFlowEvent),
}

#[cfg(feature = "jito-shredstream")]
fn emit_teacher_impulse_observation_feed(
    cfg: &Config,
    observations: &[NativePumpfunObservation],
) -> Result<()> {
    if observations.is_empty() || !parse_env_bool("TEACHER_IMPULSE_OBSERVATION_FEED_ENABLED", false)
    {
        return Ok(());
    }
    let Some(path) = env_option_string("TEACHER_IMPULSE_OBSERVATION_FEED_FILE") else {
        return Ok(());
    };
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .with_context(|| format!("open teacher impulse observation feed {path}"))?;
    for observation in observations {
        let normalized = teacher_impulse_observation_from_native(cfg, observation);
        writeln!(file, "{}", serde_json::to_string(&normalized)?)
            .with_context(|| format!("write teacher impulse observation feed {path}"))?;
    }
    Ok(())
}

#[cfg(feature = "jito-shredstream")]
fn teacher_impulse_observation_from_native(
    cfg: &Config,
    observation: &NativePumpfunObservation,
) -> TeacherImpulseObservation {
    match observation {
        NativePumpfunObservation::Creation {
            mint,
            signature,
            slot,
            transaction_index,
            observed_at_ms,
            token_program_id,
            bonding_curve,
        } => TeacherImpulseObservation {
            source: ProviderSource::ShredStream,
            observation_id: Some(format!(
                "shred-native-create:{slot}:{transaction_index}:{signature}:{mint}"
            )),
            raw_record_identity: Some(format!(
                "shred-native-create:{slot}:{transaction_index}:{signature}:{mint}"
            )),
            raw_record_hash: None,
            raw_record: Some(json!({
                "kind": "teacherImpulseNativeObservation",
                "nativeKind": "creation",
                "source": "jito-shredstream",
                "slot": slot,
                "transactionIndex": transaction_index,
                "signature": signature,
                "mint": mint,
                "tokenProgram": token_program_id,
                "bondingCurve": bonding_curve,
            })),
            source_slot: Some(*slot),
            source_transaction_index: u32::try_from(*transaction_index).ok(),
            provider_sequence: Some(format!("jito-shredstream:{slot}:{transaction_index}")),
            local_receive_ts_ms: *observed_at_ms,
            decode_complete_ts_ms: Some(*observed_at_ms),
            transaction_signature: Some(signature.clone()),
            mint: mint.clone(),
            token_program: Some(token_program_id.clone()),
            source_ix_data_base64: None,
            creation_proven: Some(true),
            creation_ts_ms: Some(*observed_at_ms),
            side: ObservationSide::Create,
            quote_sol: None,
            quote_lamports: None,
            sell_quote_lamports: None,
            sell_token_amount_raw: None,
            wallet: None,
            canonical_state: None,
            mayhem: Some(MayhemEvidence {
                status: MayhemStatus::Unknown,
                reason: Some("mayhem_unproven_by_native_observation_feed".to_string()),
                checked_at_ms: None,
            }),
            execution: None,
            extra: json!({
                "adapter": "native_shredstream_observation_feed",
                "teacherWalletMatched": false,
            }),
        },
        NativePumpfunObservation::Flow(flow) => {
            let teacher_wallet_matched = cfg
                .tracked_wallets
                .iter()
                .any(|wallet| wallet == &flow.wallet);
            let side = match flow.side {
                NativeFlowSide::Buy => ObservationSide::Buy,
                NativeFlowSide::Sell => ObservationSide::Sell,
            };
            TeacherImpulseObservation {
                source: ProviderSource::ShredStream,
                observation_id: Some(format!(
                    "shred-native-flow:{}:{}:{}:{}:{}",
                    flow.slot,
                    flow.transaction_index,
                    flow.instruction_index,
                    flow.signature,
                    flow.mint
                )),
                raw_record_identity: Some(format!(
                    "shred-native-flow:{}:{}:{}:{}:{}",
                    flow.slot,
                    flow.transaction_index,
                    flow.instruction_index,
                    flow.signature,
                    flow.mint
                )),
                raw_record_hash: None,
                raw_record: Some(json!({
                    "kind": "teacherImpulseNativeObservation",
                    "nativeKind": "flow",
                    "source": "jito-shredstream",
                    "slot": flow.slot,
                    "transactionIndex": flow.transaction_index,
                    "instructionIndex": flow.instruction_index,
                    "signature": flow.signature,
                    "wallet": flow.wallet,
                    "mint": flow.mint,
                    "side": side,
                    "quoteLamports": flow.quote_lamports,
                    "sellQuoteLamports": flow.sell_quote_lamports,
                    "sellTokenAmountRaw": flow.sell_token_amount_raw,
                    "bondingCurve": flow.bonding_curve,
                    "tokenProgram": flow.token_program_id,
                    "teacherWalletMatched": teacher_wallet_matched,
                })),
                source_slot: Some(flow.slot),
                source_transaction_index: u32::try_from(flow.transaction_index).ok(),
                provider_sequence: Some(format!(
                    "jito-shredstream:{}:{}:{}",
                    flow.slot, flow.transaction_index, flow.instruction_index
                )),
                local_receive_ts_ms: flow.observed_at_ms,
                decode_complete_ts_ms: Some(flow.observed_at_ms),
                transaction_signature: Some(flow.signature.clone()),
                mint: flow.mint.clone(),
                token_program: Some(flow.token_program_id.clone()),
                source_ix_data_base64: Some(flow.source_ix_data_base64.clone()),
                creation_proven: None,
                creation_ts_ms: None,
                side,
                quote_sol: flow
                    .quote_lamports
                    .or(flow.sell_quote_lamports)
                    .map(|lamports| lamports as f64 / LAMPORTS_PER_SOL),
                quote_lamports: flow.quote_lamports,
                sell_quote_lamports: flow.sell_quote_lamports,
                sell_token_amount_raw: flow.sell_token_amount_raw,
                wallet: Some(flow.wallet.clone()),
                canonical_state: None,
                mayhem: Some(native_observation_mayhem_evidence(flow)),
                execution: None,
                extra: json!({
                    "adapter": "native_shredstream_observation_feed",
                    "bondingCurve": flow.bonding_curve,
                    "sourceIxAccounts": flow.source_ix_accounts,
                    "teacherWalletMatched": teacher_wallet_matched,
                }),
            }
        }
    }
}

#[cfg(feature = "jito-shredstream")]
fn native_observation_mayhem_evidence(flow: &NativeFlowEvent) -> MayhemEvidence {
    if flow.token_program_id != TOKEN_2022_PROGRAM_ID {
        return MayhemEvidence {
            status: MayhemStatus::Clean,
            reason: Some("legacy_token_program_not_mayhem".to_string()),
            checked_at_ms: Some(flow.observed_at_ms),
        };
    }
    let mayhem_account = env::var("MAYHEM_SKIP_ACCOUNT")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e".to_string());
    match mayhem_account {
        account if flow.source_ix_accounts.iter().any(|seen| seen == &account) => MayhemEvidence {
            status: MayhemStatus::HardBlock,
            reason: Some("native_source_ix_mayhem_account".to_string()),
            checked_at_ms: Some(flow.observed_at_ms),
        },
        _ => MayhemEvidence {
            status: MayhemStatus::Clean,
            reason: Some("native_source_ix_mayhem_account_absent".to_string()),
            checked_at_ms: Some(flow.observed_at_ms),
        },
    }
}

impl Default for CandidateCurveCache {
    fn default() -> Self {
        Self {
            watched: Arc::new(Mutex::new(HashSet::new())),
            states: Arc::new(Mutex::new(HashMap::new())),
            yellowstone_states: Arc::new(Mutex::new(HashMap::new())),
            max_states: parse_env_u64("RUST_SELECTED_CANDIDATE_CURVE_CACHE_MAX_STATES", 100_000)
                .clamp(1_000, 1_000_000) as usize,
            prune_every_updates: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_CURVE_CACHE_PRUNE_EVERY_UPDATES",
                512,
            )
            .clamp(32, 16_384),
            update_count: Arc::new(AtomicU64::new(0)),
            account_update_count: Arc::new(AtomicU64::new(0)),
            decode_success_count: Arc::new(AtomicU64::new(0)),
            decode_skip_count: Arc::new(AtomicU64::new(0)),
            yellowstone_account_update_count: Arc::new(AtomicU64::new(0)),
            yellowstone_decode_success_count: Arc::new(AtomicU64::new(0)),
            yellowstone_decode_skip_count: Arc::new(AtomicU64::new(0)),
        }
    }
}

#[derive(Debug, Clone)]
struct CachedCurveAccount {
    state: CurveState,
    slot: u64,
    observed_at_ms: u64,
    source: &'static str,
}

#[derive(Debug, Clone)]
struct CachedBlockhash {
    value: String,
    fetched_at_ms: u128,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SelectedCandidatePostFinalityExitAction {
    KeepAlreadyActivated,
    Activate,
    DeferAwaitProof,
    CancelMissingPosition,
}

fn selected_candidate_post_finality_exit_action(
    exit_watch_activated: bool,
    activate_exit_watch: bool,
    defer_await_proof: bool,
    exact_balance_proof_seen: bool,
) -> SelectedCandidatePostFinalityExitAction {
    if exit_watch_activated {
        SelectedCandidatePostFinalityExitAction::KeepAlreadyActivated
    } else if activate_exit_watch {
        SelectedCandidatePostFinalityExitAction::Activate
    } else if defer_await_proof || exact_balance_proof_seen {
        SelectedCandidatePostFinalityExitAction::DeferAwaitProof
    } else {
        SelectedCandidatePostFinalityExitAction::CancelMissingPosition
    }
}

fn selected_candidate_independent_curve_source(source: &str) -> bool {
    matches!(
        source,
        "yellowstone-cache"
            | "native-shred-feature"
            | "selected-event-recovered-native"
            | "raw-candidate-feature-snapshot"
    )
}

fn selected_candidate_hard_stop_authoritative_curve_source(source: &str) -> bool {
    matches!(
        source,
        "yellowstone-cache"
            | "yellowstone-exit-account"
            | "native-shred-feature"
            | "selected-event-recovered-native"
    )
}

fn selected_candidate_cache_hit_allowed(
    cfg: &SelectedCandidateSniperConfig,
    cached: &CachedCurveAccount,
) -> bool {
    !cfg.curve_cache_require_independent
        || selected_candidate_independent_curve_source(cached.source)
}

fn selected_candidate_final_guard_curve_source_allowed(
    cfg: &SelectedCandidateSniperConfig,
    cached: &CachedCurveAccount,
) -> bool {
    if cfg.submit_enabled
        && selected_candidate_fill_first_buy_policy(cfg)
        && parse_env_bool(
            "RUST_SELECTED_CANDIDATE_FILL_FIRST_FINAL_GUARD_YELLOWSTONE_REQUIRED",
            true,
        )
    {
        return cached.source == "yellowstone-cache";
    }
    selected_candidate_cache_hit_allowed(cfg, cached)
}

fn selected_candidate_expected_output_drop_bps(
    earlier_tokens_raw: u64,
    newer_tokens_raw: u64,
) -> Option<u64> {
    if earlier_tokens_raw == 0 {
        return None;
    }
    if newer_tokens_raw >= earlier_tokens_raw {
        return Some(0);
    }
    let dropped = earlier_tokens_raw.saturating_sub(newer_tokens_raw) as u128;
    Some(((dropped * 10_000u128) / earlier_tokens_raw as u128).min(u64::MAX as u128) as u64)
}

impl CandidateCurveCache {
    fn watch(&self, bonding_curve: &str) {
        if bonding_curve.trim().is_empty() {
            return;
        }
        if let Ok(mut watched) = self.watched.lock() {
            watched.insert(bonding_curve.to_string());
        }
    }

    fn watched_curves(&self) -> Vec<String> {
        let mut curves = self
            .watched
            .lock()
            .expect("candidate curve cache watched mutex poisoned")
            .iter()
            .cloned()
            .collect::<Vec<_>>();
        curves.sort();
        curves.dedup();
        curves
    }

    fn update(&self, bonding_curve: String, state: CurveState, slot: u64, observed_at_ms: u64) {
        self.update_from_source(
            bonding_curve,
            state,
            slot,
            observed_at_ms,
            "yellowstone-cache",
        );
    }

    fn update_from_source(
        &self,
        bonding_curve: String,
        mut state: CurveState,
        slot: u64,
        observed_at_ms: u64,
        source: &'static str,
    ) {
        if source == "yellowstone-cache" {
            self.update_yellowstone_state(
                bonding_curve.clone(),
                state.clone(),
                slot,
                observed_at_ms,
                source,
            );
        }
        if let Ok(mut states) = self.states.lock() {
            if let Some(existing) = states.get(&bonding_curve) {
                if state.creator.is_none() {
                    state.creator = existing.state.creator.clone();
                }
                let existing_independent =
                    selected_candidate_independent_curve_source(existing.source);
                let incoming_independent = selected_candidate_independent_curve_source(source);
                let should_promote_independent = incoming_independent && !existing_independent;
                if existing.observed_at_ms > observed_at_ms && !should_promote_independent {
                    return;
                }
            }
            states.insert(
                bonding_curve,
                CachedCurveAccount {
                    state,
                    slot,
                    observed_at_ms,
                    source,
                },
            );
            let updates = self.update_count.fetch_add(1, Ordering::Relaxed) + 1;
            let over_capacity = states.len().saturating_sub(self.max_states);
            if over_capacity > 0
                && (updates % self.prune_every_updates == 0
                    || over_capacity >= self.prune_every_updates as usize)
            {
                let mut oldest = states
                    .iter()
                    .map(|(key, cached)| (key.clone(), cached.observed_at_ms))
                    .collect::<Vec<_>>();
                oldest.sort_by_key(|(_, observed_at_ms)| *observed_at_ms);
                for (key, _) in oldest.into_iter().take(over_capacity) {
                    states.remove(&key);
                }
            }
        }
    }

    fn update_yellowstone_state(
        &self,
        bonding_curve: String,
        mut state: CurveState,
        slot: u64,
        observed_at_ms: u64,
        source: &'static str,
    ) {
        if let Ok(mut states) = self.yellowstone_states.lock() {
            if let Some(existing) = states.get(&bonding_curve) {
                if state.creator.is_none() {
                    state.creator = existing.state.creator.clone();
                }
                if existing.observed_at_ms > observed_at_ms {
                    return;
                }
            }
            states.insert(
                bonding_curve,
                CachedCurveAccount {
                    state,
                    slot,
                    observed_at_ms,
                    source,
                },
            );
            let over_capacity = states.len().saturating_sub(self.max_states);
            if over_capacity > 0 {
                let mut oldest = states
                    .iter()
                    .map(|(key, cached)| (key.clone(), cached.observed_at_ms))
                    .collect::<Vec<_>>();
                oldest.sort_by_key(|(_, observed_at_ms)| *observed_at_ms);
                for (key, _) in oldest.into_iter().take(over_capacity) {
                    states.remove(&key);
                }
            }
        }
    }

    fn update_from_native_feature(
        &self,
        bonding_curve: String,
        virtual_sol_reserves: u64,
        virtual_token_reserves: u64,
        slot: u64,
        observed_at_ms: u64,
    ) {
        self.watch(&bonding_curve);
        self.mark_account_update();
        self.mark_decode_success();
        self.update_from_source(
            bonding_curve,
            CurveState {
                virtual_token_reserves,
                virtual_sol_reserves,
                token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
                complete: false,
                creator: None,
            },
            slot,
            observed_at_ms,
            "native-shred-feature",
        );
    }

    fn mark_account_update(&self) {
        self.account_update_count.fetch_add(1, Ordering::Relaxed);
    }

    fn mark_decode_success(&self) {
        self.decode_success_count.fetch_add(1, Ordering::Relaxed);
    }

    fn mark_decode_skip(&self) {
        self.decode_skip_count.fetch_add(1, Ordering::Relaxed);
    }

    fn mark_yellowstone_account_update(&self) {
        self.yellowstone_account_update_count
            .fetch_add(1, Ordering::Relaxed);
    }

    fn mark_yellowstone_decode_success(&self) {
        self.yellowstone_decode_success_count
            .fetch_add(1, Ordering::Relaxed);
    }

    fn mark_yellowstone_decode_skip(&self) {
        self.yellowstone_decode_skip_count
            .fetch_add(1, Ordering::Relaxed);
    }

    fn account_update_count(&self) -> u64 {
        self.account_update_count.load(Ordering::Relaxed)
    }

    fn decode_success_count(&self) -> u64 {
        self.decode_success_count.load(Ordering::Relaxed)
    }

    fn decode_skip_count(&self) -> u64 {
        self.decode_skip_count.load(Ordering::Relaxed)
    }

    fn yellowstone_account_update_count(&self) -> u64 {
        self.yellowstone_account_update_count
            .load(Ordering::Relaxed)
    }

    fn yellowstone_decode_success_count(&self) -> u64 {
        self.yellowstone_decode_success_count
            .load(Ordering::Relaxed)
    }

    fn yellowstone_decode_skip_count(&self) -> u64 {
        self.yellowstone_decode_skip_count.load(Ordering::Relaxed)
    }

    fn get_fresh(&self, bonding_curve: &str, max_age_ms: u64) -> Option<CachedCurveAccount> {
        let now = now_ms().min(u64::MAX as u128) as u64;
        self.states
            .lock()
            .ok()?
            .get(bonding_curve)
            .filter(|cached| now.saturating_sub(cached.observed_at_ms) <= max_age_ms)
            .cloned()
    }

    fn get_fresh_yellowstone(
        &self,
        bonding_curve: &str,
        max_age_ms: u64,
    ) -> Option<CachedCurveAccount> {
        let now = now_ms().min(u64::MAX as u128) as u64;
        self.yellowstone_states
            .lock()
            .ok()?
            .get(bonding_curve)
            .filter(|cached| cached.source == "yellowstone-cache")
            .filter(|cached| now.saturating_sub(cached.observed_at_ms) <= max_age_ms)
            .cloned()
    }

    fn get_fresh_hard_stop_authoritative(
        &self,
        bonding_curve: &str,
        max_age_ms: u64,
    ) -> Option<CachedCurveAccount> {
        if let Some(cached) = self.get_fresh_yellowstone(bonding_curve, max_age_ms) {
            return Some(cached);
        }
        let now = now_ms().min(u64::MAX as u128) as u64;
        self.states
            .lock()
            .ok()?
            .get(bonding_curve)
            .filter(|cached| selected_candidate_hard_stop_authoritative_curve_source(cached.source))
            .filter(|cached| now.saturating_sub(cached.observed_at_ms) <= max_age_ms)
            .cloned()
    }

    fn state_count(&self) -> usize {
        self.states.lock().map(|states| states.len()).unwrap_or(0)
    }

    fn yellowstone_state_count(&self) -> usize {
        self.yellowstone_states
            .lock()
            .map(|states| states.len())
            .unwrap_or(0)
    }
}

#[cfg(feature = "jito-shredstream")]
#[derive(Debug, Clone)]
struct ShredReplayUpdate {
    state: CurveState,
    seed_slot: u64,
    seed_observed_at_ms: u64,
    seed_source: &'static str,
    last_slot: u64,
    last_transaction_index: u64,
    last_instruction_index: u32,
    updated_at_ms: u64,
    applied_flow: bool,
    replay_source: &'static str,
}

impl SelectedCandidateShredCurveReplay {
    fn seed_from_selected_candidate_snapshot(
        &self,
        bonding_curve: &str,
        state: CurveState,
        slot: u64,
        observed_at_ms: u64,
        source: &'static str,
    ) -> Result<bool, &'static str> {
        let mut states = self
            .states
            .lock()
            .map_err(|_| "replay_state_lock_poisoned")?;
        if let Some(existing) = states.get(bonding_curve) {
            if (existing.last_slot, existing.updated_at_ms) >= (slot, observed_at_ms) {
                return Ok(false);
            }
        }
        states.insert(
            bonding_curve.to_string(),
            ShredReplayCurveState {
                state,
                seed_slot: slot,
                seed_observed_at_ms: observed_at_ms,
                seed_source: source,
                last_slot: slot,
                last_transaction_index: 0,
                last_instruction_index: 0,
                updated_at_ms: observed_at_ms,
            },
        );
        Ok(true)
    }
}

#[cfg(feature = "jito-shredstream")]
impl SelectedCandidateShredCurveReplay {
    fn update_from_canonical_seed(
        &self,
        curve_cache: &CandidateCurveCache,
        flow: &NativeFlowEvent,
        seed_max_age_ms: u64,
        state_max_age_ms: u64,
    ) -> Result<Option<ShredReplayUpdate>, &'static str> {
        let now = now_ms().min(u64::MAX as u128) as u64;
        let order = (flow.slot, flow.transaction_index, flow.instruction_index);
        let mut states = self
            .states
            .lock()
            .map_err(|_| "replay_state_lock_poisoned")?;
        if let Some(existing) = states.get_mut(&flow.bonding_curve) {
            let existing_order = (
                existing.last_slot,
                existing.last_transaction_index,
                existing.last_instruction_index,
            );
            if order <= existing_order {
                return Err("duplicate_or_out_of_order_flow");
            }
            if now.saturating_sub(existing.updated_at_ms) <= state_max_age_ms {
                apply_native_flow_to_curve_state(&mut existing.state, flow)
                    .ok_or("flow_apply_failed")?;
                existing.last_slot = flow.slot;
                existing.last_transaction_index = flow.transaction_index;
                existing.last_instruction_index = flow.instruction_index;
                existing.updated_at_ms = flow.observed_at_ms;
                return Ok(Some(ShredReplayUpdate {
                    state: existing.state.clone(),
                    seed_slot: existing.seed_slot,
                    seed_observed_at_ms: existing.seed_observed_at_ms,
                    seed_source: existing.seed_source,
                    last_slot: existing.last_slot,
                    last_transaction_index: existing.last_transaction_index,
                    last_instruction_index: existing.last_instruction_index,
                    updated_at_ms: existing.updated_at_ms,
                    applied_flow: true,
                    replay_source: "native-shred-canonical-replay",
                }));
            }
        }

        let cached = curve_cache
            .get_fresh_yellowstone(&flow.bonding_curve, seed_max_age_ms)
            .ok_or("canonical_seed_missing")?;
        if cached.state.complete {
            return Err("canonical_seed_curve_complete");
        }
        let mut replay_state = cached.state.clone();
        let mut applied_flow = false;
        let replay_source = if cached.slot >= flow.slot {
            "yellowstone-cache-at-or-after-flow"
        } else {
            apply_native_flow_to_curve_state(&mut replay_state, flow).ok_or("flow_apply_failed")?;
            applied_flow = true;
            "native-shred-canonical-replay"
        };
        let entry = ShredReplayCurveState {
            state: replay_state.clone(),
            seed_slot: cached.slot,
            seed_observed_at_ms: cached.observed_at_ms,
            seed_source: cached.source,
            last_slot: flow.slot,
            last_transaction_index: flow.transaction_index,
            last_instruction_index: flow.instruction_index,
            updated_at_ms: flow.observed_at_ms,
        };
        states.insert(flow.bonding_curve.clone(), entry.clone());
        Ok(Some(ShredReplayUpdate {
            state: replay_state,
            seed_slot: entry.seed_slot,
            seed_observed_at_ms: entry.seed_observed_at_ms,
            seed_source: entry.seed_source,
            last_slot: entry.last_slot,
            last_transaction_index: entry.last_transaction_index,
            last_instruction_index: entry.last_instruction_index,
            updated_at_ms: entry.updated_at_ms,
            applied_flow,
            replay_source,
        }))
    }
}

#[cfg(feature = "jito-shredstream")]
fn apply_native_flow_to_curve_state(state: &mut CurveState, flow: &NativeFlowEvent) -> Option<()> {
    match flow.side {
        NativeFlowSide::Buy => {
            let quote = flow.quote_lamports? as u128;
            let reserve_quote = pumpfun_exact_sol_in_curve_lamports_main(quote).ok()?;
            let tokens_out = quote_buy_tokens_out_raw_main(
                state.virtual_sol_reserves as u128,
                state.virtual_token_reserves as u128,
                quote,
            )
            .ok()?;
            let next_sol = (state.virtual_sol_reserves as u128).checked_add(reserve_quote)?;
            let next_tokens = (state.virtual_token_reserves as u128).checked_sub(tokens_out)?;
            state.virtual_sol_reserves = u64::try_from(next_sol).ok()?;
            state.virtual_token_reserves = u64::try_from(next_tokens).ok()?;
        }
        NativeFlowSide::Sell => {
            let tokens_in = flow.sell_token_amount_raw? as u128;
            if tokens_in == 0 {
                return None;
            }
            let pre_sol = state.virtual_sol_reserves as u128;
            let pre_tokens = state.virtual_token_reserves as u128;
            let post_tokens = pre_tokens.checked_add(tokens_in)?;
            let invariant = pre_sol.checked_mul(pre_tokens)?;
            let post_sol = invariant / post_tokens;
            state.virtual_sol_reserves = u64::try_from(post_sol).ok()?;
            state.virtual_token_reserves = u64::try_from(post_tokens).ok()?;
        }
    }
    Some(())
}

fn selected_candidate_ata_for_mint(
    mint: &str,
    token_program_id: Option<&str>,
) -> Result<(String, String)> {
    let token_program = token_program_id.unwrap_or(TOKEN_2022_PROGRAM_ID);
    let token_program_pubkey =
        Pubkey::from_str(token_program).context("selected candidate token program invalid")?;
    let mint_pubkey = Pubkey::from_str(mint).context("selected candidate mint invalid")?;
    let owner = selected_candidate_wallet_pubkey()?;
    let ata = derive_associated_token_address_main(&owner, &mint_pubkey, &token_program_pubkey)?;
    Ok((ata.to_string(), token_program.to_string()))
}

impl SelectedCandidateBalanceCache {
    fn is_pending_submitted_buy_signature(&self, signature: &str) -> bool {
        self.submitted_buy_proofs
            .lock()
            .map(|submitted_buy_proofs| submitted_buy_proofs.contains_key(signature))
            .unwrap_or(false)
    }

    fn watch_candidate(&self, candidate: &SelectedCandidate) -> Result<String> {
        let (ata, token_program_id) = selected_candidate_ata_for_mint(
            &candidate.mint,
            candidate.token_program_id.as_deref(),
        )?;
        if let Ok(mut watched) = self.watched.lock() {
            watched
                .entry(ata.clone())
                .or_insert_with(|| WatchedTokenAccount {
                    mint: candidate.mint.clone(),
                    token_program_id,
                    watched_at_ms: now_ms().min(u64::MAX as u128) as u64,
                });
        }
        Ok(ata)
    }

    fn watched_atas(&self) -> Vec<String> {
        let mut atas = self
            .watched
            .lock()
            .expect("selected candidate balance cache watched mutex poisoned")
            .keys()
            .cloned()
            .collect::<Vec<_>>();
        atas.sort();
        atas.dedup();
        atas
    }

    fn watched_token_accounts(&self) -> Vec<WatchedTokenAccountSubscription> {
        let mut accounts = self
            .watched
            .lock()
            .expect("selected candidate balance cache watched mutex poisoned")
            .iter()
            .map(|(ata, watched)| WatchedTokenAccountSubscription {
                ata: ata.clone(),
                mint: watched.mint.clone(),
                token_program_id: watched.token_program_id.clone(),
            })
            .collect::<Vec<_>>();
        accounts.sort_by(|a, b| {
            a.token_program_id
                .cmp(&b.token_program_id)
                .then_with(|| a.mint.cmp(&b.mint))
                .then_with(|| a.ata.cmp(&b.ata))
        });
        accounts.dedup_by(|a, b| {
            a.ata == b.ata && a.mint == b.mint && a.token_program_id == b.token_program_id
        });
        accounts
    }

    fn update(
        &self,
        ata: String,
        mint: String,
        owner: String,
        amount_raw: u64,
        slot: u64,
        observed_at_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        let watched = self.watched.lock().ok()?.get(&ata).cloned()?;
        if watched.mint != mint {
            return None;
        }
        let expected_owner = selected_candidate_wallet_pubkey().ok()?.to_string();
        if owner != expected_owner {
            return None;
        }
        let cached = CachedTokenAccountBalance {
            ata: ata.clone(),
            mint,
            owner,
            token_program_id: watched.token_program_id,
            amount_raw,
            slot,
            observed_at_ms,
            exact: true,
            source: "yellowstone-account",
        };
        if let Ok(mut balances) = self.balances.lock() {
            balances.insert(ata, cached.clone());
        }
        if let Ok(mut balances_by_mint) = self.balances_by_mint.lock() {
            balances_by_mint.insert(cached.mint.clone(), cached.clone());
        }
        self.remember_first_nonzero_exact(&cached);
        self.notify.notify_waiters();
        Some(cached)
    }

    fn update_wallet_owned(
        &self,
        ata: String,
        mint: String,
        owner: String,
        token_program_id: String,
        amount_raw: u64,
        slot: u64,
        observed_at_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        let expected_owner = selected_candidate_wallet_pubkey().ok()?.to_string();
        if owner != expected_owner {
            return None;
        }
        let cached = CachedTokenAccountBalance {
            ata: ata.clone(),
            mint,
            owner,
            token_program_id,
            amount_raw,
            slot,
            observed_at_ms,
            exact: true,
            source: "yellowstone-wallet-owned",
        };
        if let Ok(mut balances) = self.balances.lock() {
            balances.insert(ata, cached.clone());
        }
        if let Ok(mut balances_by_mint) = self.balances_by_mint.lock() {
            balances_by_mint.insert(cached.mint.clone(), cached.clone());
        }
        self.remember_first_nonzero_exact(&cached);
        self.notify.notify_waiters();
        Some(cached)
    }

    fn update_watched_owner_amount_slice(
        &self,
        ata: String,
        account_owner: String,
        owner: String,
        amount_raw: u64,
        slot: u64,
        observed_at_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        let watched = self.watched.lock().ok()?.get(&ata).cloned()?;
        if account_owner != watched.token_program_id {
            return None;
        }
        let expected_owner = selected_candidate_wallet_pubkey().ok()?.to_string();
        if owner != expected_owner {
            return None;
        }
        let cached = CachedTokenAccountBalance {
            ata: ata.clone(),
            mint: watched.mint,
            owner,
            token_program_id: watched.token_program_id,
            amount_raw,
            slot,
            observed_at_ms,
            exact: true,
            source: "yellowstone-account-slice",
        };
        if let Ok(mut balances) = self.balances.lock() {
            balances.insert(ata, cached.clone());
        }
        if let Ok(mut balances_by_mint) = self.balances_by_mint.lock() {
            balances_by_mint.insert(cached.mint.clone(), cached.clone());
        }
        self.remember_first_nonzero_exact(&cached);
        self.notify.notify_waiters();
        Some(cached)
    }

    fn remember_first_nonzero_exact(&self, cached: &CachedTokenAccountBalance) {
        if !cached.exact || cached.amount_raw == 0 {
            return;
        }
        if let Ok(mut first_by_ata) = self.first_nonzero_exact_by_ata.lock() {
            first_by_ata
                .entry(cached.ata.clone())
                .or_insert_with(|| cached.clone());
        }
        if let Ok(mut first_by_mint) = self.first_nonzero_exact_by_mint.lock() {
            first_by_mint
                .entry(cached.mint.clone())
                .or_insert_with(|| cached.clone());
        }
    }

    fn get_fresh_for_candidate(
        &self,
        candidate: &SelectedCandidate,
        max_age_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        self.get_fresh_for_candidate_with_exact(candidate, max_age_ms, false)
    }

    fn get_fresh_exact_for_candidate(
        &self,
        candidate: &SelectedCandidate,
        max_age_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        self.get_fresh_for_candidate_with_exact(candidate, max_age_ms, true)
    }

    fn get_fresh_nonzero_by_mint(
        &self,
        mint: &str,
        max_age_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        let now = now_ms().min(u64::MAX as u128) as u64;
        self.balances_by_mint
            .lock()
            .ok()?
            .get(mint)
            .filter(|cached| {
                cached.amount_raw > 0 && now.saturating_sub(cached.observed_at_ms) <= max_age_ms
            })
            .cloned()
    }

    fn get_fresh_exact_by_mint(
        &self,
        mint: &str,
        max_age_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        let now = now_ms().min(u64::MAX as u128) as u64;
        self.balances_by_mint
            .lock()
            .ok()?
            .get(mint)
            .filter(|cached| {
                cached.exact && now.saturating_sub(cached.observed_at_ms) <= max_age_ms
            })
            .cloned()
    }

    fn get_fresh_for_candidate_with_exact(
        &self,
        candidate: &SelectedCandidate,
        max_age_ms: u64,
        require_exact: bool,
    ) -> Option<CachedTokenAccountBalance> {
        let (ata, _) =
            selected_candidate_ata_for_mint(&candidate.mint, candidate.token_program_id.as_deref())
                .ok()?;
        let now = now_ms().min(u64::MAX as u128) as u64;
        if let Some(cached) = self
            .balances
            .lock()
            .ok()?
            .get(&ata)
            .filter(|cached| {
                now.saturating_sub(cached.observed_at_ms) <= max_age_ms
                    && (!require_exact || cached.exact)
                    && (!require_exact || cached.amount_raw > 0)
                    && candidate
                        .token_program_id
                        .as_ref()
                        .map(|expected| cached.token_program_id == *expected)
                        .unwrap_or(true)
            })
            .cloned()
        {
            return Some(cached);
        }
        if require_exact {
            if let Some(cached) = self
                .first_nonzero_exact_by_ata
                .lock()
                .ok()?
                .get(&ata)
                .filter(|cached| {
                    now.saturating_sub(cached.observed_at_ms) <= max_age_ms
                        && candidate
                            .token_program_id
                            .as_ref()
                            .map(|expected| cached.token_program_id == *expected)
                            .unwrap_or(true)
                })
                .cloned()
            {
                return Some(cached);
            }
        }
        self.balances_by_mint
            .lock()
            .ok()?
            .get(&candidate.mint)
            .filter(|cached| {
                now.saturating_sub(cached.observed_at_ms) <= max_age_ms
                    && (!require_exact || cached.exact)
                    && (!require_exact || cached.amount_raw > 0)
                    && candidate
                        .token_program_id
                        .as_ref()
                        .map(|expected| cached.token_program_id == *expected)
                        .unwrap_or(true)
            })
            .cloned()
            .or_else(|| {
                if !require_exact {
                    return None;
                }
                self.first_nonzero_exact_by_mint
                    .lock()
                    .ok()?
                    .get(&candidate.mint)
                    .filter(|cached| {
                        now.saturating_sub(cached.observed_at_ms) <= max_age_ms
                            && candidate
                                .token_program_id
                                .as_ref()
                                .map(|expected| cached.token_program_id == *expected)
                                .unwrap_or(true)
                    })
                    .cloned()
            })
    }

    fn remember_submitted_buy(
        &self,
        signature: String,
        candidate: &SelectedCandidate,
        amount_raw: u64,
    ) -> Result<Option<CachedTokenAccountBalance>> {
        let (ata, token_program_id) = selected_candidate_ata_for_mint(
            &candidate.mint,
            candidate.token_program_id.as_deref(),
        )?;
        let proof = SubmittedBuyTokenProof {
            ata,
            mint: candidate.mint.clone(),
            owner: selected_candidate_wallet_pubkey()?.to_string(),
            token_program_id,
            amount_raw,
            observed_at_ms: now_ms().min(u64::MAX as u128) as u64,
        };
        if let Ok(mut submitted_buy_proofs) = self.submitted_buy_proofs.lock() {
            submitted_buy_proofs.insert(signature.clone(), proof);
        }
        if !selected_candidate_signature_only_balance_proof_enabled() {
            return Ok(None);
        }
        let preseen = self
            .recent_shredstream_signatures
            .lock()
            .ok()
            .and_then(|seen| seen.get(&signature).cloned());
        Ok(preseen.and_then(|(slot, observed_at_ms)| {
            self.commit_submitted_buy_landed_signature_with_source(
                &signature,
                slot,
                observed_at_ms,
                "jito-shredstream-submitted-buy-signature",
            )
        }))
    }

    fn remember_shredstream_transaction_signature(
        &self,
        signature: &str,
        slot: u64,
        observed_at_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        if selected_candidate_signature_only_balance_proof_enabled() {
            if let Some(cached) = self.commit_submitted_buy_landed_signature_with_source(
                signature,
                slot,
                observed_at_ms,
                "jito-shredstream-submitted-buy-signature",
            ) {
                return Some(cached);
            }
        }
        if let Ok(mut seen) = self.recent_shredstream_signatures.lock() {
            seen.insert(signature.to_string(), (slot, observed_at_ms));
            if seen.len() > 100_000 {
                let cutoff = observed_at_ms.saturating_sub(30_000);
                seen.retain(|_, (_, seen_at_ms)| *seen_at_ms >= cutoff);
                if seen.len() > 100_000 {
                    let mut by_age = seen
                        .iter()
                        .map(|(signature, (_, seen_at_ms))| (signature.clone(), *seen_at_ms))
                        .collect::<Vec<_>>();
                    by_age.sort_by_key(|(_, seen_at_ms)| *seen_at_ms);
                    let remove_count = seen.len().saturating_sub(75_000);
                    for (signature, _) in by_age.into_iter().take(remove_count) {
                        seen.remove(&signature);
                    }
                }
            }
        }
        None
    }

    fn commit_submitted_buy_landed_signature(
        &self,
        signature: &str,
        slot: u64,
        observed_at_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        self.commit_submitted_buy_landed_signature_with_source(
            signature,
            slot,
            observed_at_ms,
            "yellowstone-successful-submitted-buy-signature",
        )
    }

    fn commit_submitted_buy_landed_signature_with_source(
        &self,
        signature: &str,
        slot: u64,
        observed_at_ms: u64,
        source: &'static str,
    ) -> Option<CachedTokenAccountBalance> {
        let proof = self
            .submitted_buy_proofs
            .lock()
            .ok()?
            .get(signature)?
            .clone();
        let cached = CachedTokenAccountBalance {
            ata: proof.ata.clone(),
            mint: proof.mint,
            owner: proof.owner,
            token_program_id: proof.token_program_id,
            amount_raw: proof.amount_raw,
            slot,
            observed_at_ms: observed_at_ms.max(proof.observed_at_ms),
            exact: false,
            source,
        };
        if let Ok(mut balances) = self.balances.lock() {
            balances.insert(proof.ata, cached.clone());
        }
        if let Ok(mut balances_by_mint) = self.balances_by_mint.lock() {
            balances_by_mint.insert(cached.mint.clone(), cached.clone());
        }
        self.notify.notify_waiters();
        Some(cached)
    }

    fn commit_submitted_buy_signature(
        &self,
        signature: &str,
        slot: u64,
        observed_at_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        let proof = self.submitted_buy_proofs.lock().ok()?.remove(signature)?;
        let cached = CachedTokenAccountBalance {
            ata: proof.ata.clone(),
            mint: proof.mint,
            owner: proof.owner,
            token_program_id: proof.token_program_id,
            amount_raw: proof.amount_raw,
            slot,
            observed_at_ms: observed_at_ms.max(proof.observed_at_ms),
            exact: false,
            source: "yellowstone-successful-submitted-buy-signature",
        };
        if let Ok(mut balances) = self.balances.lock() {
            balances.insert(proof.ata, cached.clone());
        }
        if let Ok(mut balances_by_mint) = self.balances_by_mint.lock() {
            balances_by_mint.insert(cached.mint.clone(), cached.clone());
        }
        self.notify.notify_waiters();
        Some(cached)
    }

    fn commit_submitted_buy_post_token_balance(
        &self,
        signature: &str,
        mint: &str,
        owner: &str,
        token_program_id: &str,
        amount_raw: u64,
        slot: u64,
        observed_at_ms: u64,
    ) -> Option<CachedTokenAccountBalance> {
        if amount_raw == 0 {
            return None;
        }
        let expected_ata = selected_candidate_ata_for_mint(mint, Some(token_program_id))
            .ok()?
            .0;
        let proof = {
            let mut submitted_buy_proofs = self.submitted_buy_proofs.lock().ok()?;
            let proof = submitted_buy_proofs.get(signature)?.clone();
            if proof.mint != mint
                || proof.owner != owner
                || proof.token_program_id != token_program_id
                || proof.ata != expected_ata
            {
                return None;
            }
            submitted_buy_proofs.remove(signature)?
        };
        let cached = CachedTokenAccountBalance {
            ata: proof.ata.clone(),
            mint: proof.mint,
            owner: proof.owner,
            token_program_id: proof.token_program_id,
            amount_raw,
            slot,
            observed_at_ms: observed_at_ms.max(proof.observed_at_ms),
            exact: true,
            source: "yellowstone-submitted-buy-post-token-balance",
        };
        if let Ok(mut balances) = self.balances.lock() {
            balances.insert(proof.ata, cached.clone());
        }
        if let Ok(mut balances_by_mint) = self.balances_by_mint.lock() {
            balances_by_mint.insert(cached.mint.clone(), cached.clone());
        }
        self.remember_first_nonzero_exact(&cached);
        self.notify.notify_waiters();
        Some(cached)
    }

    fn discard_submitted_buy_signature(&self, signature: &str) -> bool {
        self.submitted_buy_proofs
            .lock()
            .ok()
            .and_then(|mut submitted_buy_proofs| submitted_buy_proofs.remove(signature))
            .is_some()
    }

    fn prune_mint(&self, mint: &str) -> Vec<String> {
        let removed_atas = self
            .watched
            .lock()
            .map(|mut watched| {
                let atas = watched
                    .iter()
                    .filter_map(|(ata, watched)| (watched.mint == mint).then_some(ata.clone()))
                    .collect::<Vec<_>>();
                for ata in &atas {
                    watched.remove(ata);
                }
                atas
            })
            .unwrap_or_default();

        if !removed_atas.is_empty() {
            if let Ok(mut balances) = self.balances.lock() {
                for ata in &removed_atas {
                    balances.remove(ata);
                }
            }
            if let Ok(mut first_by_ata) = self.first_nonzero_exact_by_ata.lock() {
                for ata in &removed_atas {
                    first_by_ata.remove(ata);
                }
            }
        }
        if let Ok(mut balances_by_mint) = self.balances_by_mint.lock() {
            balances_by_mint.remove(mint);
        }
        if let Ok(mut first_by_mint) = self.first_nonzero_exact_by_mint.lock() {
            first_by_mint.remove(mint);
        }
        if let Ok(mut submitted_buy_proofs) = self.submitted_buy_proofs.lock() {
            submitted_buy_proofs.retain(|_, proof| proof.mint != mint);
        }
        removed_atas
    }
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
    source_pump_swap_instruction: Option<&'static str>,
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

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct HotBuyFallbackLine {
    kind: &'static str,
    source: &'static str,
    detected_at_ms: u128,
    source_signature: String,
    wallet: String,
    mint: String,
    reason: String,
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let cfg = Config::from_env()?;
    let exit_watcher = cfg
        .exit_watcher_enabled
        .then(exit_watcher::ExitWatcherRegistry::default);
    let (exit_action_tx, exit_action_rx) = mpsc::unbounded_channel();
    if parse_env_bool("RUST_EXECUTOR_ENABLED", false) {
        let shared_bought_mints = if cfg.hot_sniper.enabled && cfg.hot_sniper.buy_once_per_token {
            Some(cfg.hot_sniper.bought_mints.clone())
        } else {
            None
        };
        // Always share the per-source-signature set so the executor command bridge
        // and the ShredStream hot sniper mirror each source tx at most once,
        // regardless of BUY_ONCE_PER_TOKEN (which only gates the per-mint set).
        let shared_bought_signatures = Some(cfg.hot_sniper.bought_signatures.clone());
        executor::start_executor_stdin(
            shared_bought_mints,
            shared_bought_signatures,
            exit_watcher.clone(),
            exit_watcher.as_ref().map(|_| exit_action_tx.clone()),
        );
    }
    eprintln!(
        "rust-sidecar: starting yellowstone={} jito_shredstream={} hot_sniper={} selected_candidate_sniper={} selected_submit={} native_strict_selector={} exit_watcher={} wallets={}",
        cfg.yellowstone_enabled,
        cfg.jito_shredstream_enabled,
        cfg.hot_sniper.enabled,
        cfg.selected_candidate_sniper.enabled,
        cfg.selected_candidate_sniper.submit_enabled,
        cfg.native_strict_selector.enabled,
        cfg.exit_watcher_enabled,
        cfg.tracked_wallets.len()
    );
    if let Some(registry) = exit_watcher.clone() {
        let hot = cfg.hot_sniper.clone();
        let action_tx = exit_action_tx.clone();
        let selected_cfg = cfg.selected_candidate_sniper.clone();
        let curve_cache = cfg.selected_candidate_curve_cache.clone();
        tokio::spawn(async move {
            run_exit_action_loop(
                exit_action_rx,
                action_tx,
                registry,
                hot,
                selected_cfg,
                curve_cache,
            )
            .await;
        });
    } else {
        drop(exit_action_rx);
    }
    if cfg.hot_sniper.enabled || cfg.exit_watcher_enabled || cfg.selected_candidate_sniper.enabled {
        if cfg.hot_sniper.enabled {
            eprintln!("rust-sidecar: hot sniper ON for Pump.fun source-replay buys");
        }
        if cfg.selected_candidate_sniper.enabled {
            eprintln!(
                "rust-sidecar: selected candidate sniper ON on {}:{} (submit={})",
                cfg.selected_candidate_sniper.bind_host,
                cfg.selected_candidate_sniper.bind_port,
                cfg.selected_candidate_sniper.submit_enabled
            );
        }
        if cfg.native_strict_selector.enabled {
            eprintln!("rust-sidecar: native strict selector ON for program-wide Pump.fun flow");
            if cfg.native_strict_selector.parity_candidate_source.is_some() {
                eprintln!("rust-sidecar: native strict selector parity guard ON");
            }
            if cfg
                .native_strict_selector
                .comparison_candidate_source
                .is_some()
            {
                eprintln!("rust-sidecar: native strict selector shadow comparison feed ON");
            }
        }
        if cfg.exit_watcher_enabled {
            eprintln!("rust-sidecar: exit watcher cached blockhash refresher ON");
        }
        let hot = cfg.hot_sniper.clone();
        tokio::spawn(async move {
            run_hot_blockhash_refresher(hot).await;
        });
        if cfg.selected_candidate_sniper.submit_enabled || cfg.hot_sniper.enabled {
            let hot = cfg.hot_sniper.clone();
            tokio::spawn(async move {
                run_broadcast_prewarm_loop(hot).await;
            });
        }
    }

    if cfg.native_strict_selector.enabled
        && (cfg.native_strict_selector.parity_candidate_source.is_some()
            || cfg
                .native_strict_selector
                .comparison_candidate_source
                .is_some())
    {
        let selector = cfg.native_strict_selector.clone();
        let selected_cfg = cfg.selected_candidate_sniper.clone();
        tokio::spawn(async move {
            run_native_strict_shadow_feed(selector, selected_cfg).await;
        });
    }

    if cfg.selected_candidate_sniper.enabled {
        if cfg.selected_candidate_sniper.bind_port == 0 {
            bail!("RUST_SELECTED_CANDIDATE_SNIPER_ENABLED=true requires RUST_SELECTED_CANDIDATE_TCP_PORT > 0");
        }
        if cfg.selected_candidate_sniper.submit_enabled && exit_watcher.is_none() {
            bail!("RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED=true requires RUST_EXIT_WATCHER_ENABLED=true so Rust-owned buys have Rust-owned exits");
        }
        let selected_cfg = cfg.selected_candidate_sniper.clone();
        let hot = cfg.hot_sniper.clone();
        let selected_curve_cache = cfg.selected_candidate_curve_cache.clone();
        let selected_shred_curve_replay = cfg.selected_candidate_shred_curve_replay.clone();
        let selected_exit_watcher = exit_watcher.clone();
        let selected_exit_actions = exit_watcher.as_ref().map(|_| exit_action_tx.clone());
        tokio::spawn(async move {
            if let Err(err) = run_selected_candidate_sniper(
                selected_cfg,
                hot,
                selected_curve_cache,
                selected_shred_curve_replay,
                selected_exit_watcher,
                selected_exit_actions,
            )
            .await
            {
                eprintln!("rust-sidecar: selected candidate sniper stopped: {err:#}");
            }
        });
        if cfg.selected_candidate_sniper.candidate_source.is_some() {
            let file_cfg = cfg.selected_candidate_sniper.clone();
            let file_hot = cfg.hot_sniper.clone();
            let file_curve_cache = cfg.selected_candidate_curve_cache.clone();
            let file_shred_curve_replay = cfg.selected_candidate_shred_curve_replay.clone();
            let file_exit_watcher = exit_watcher.clone();
            let file_exit_actions = exit_watcher.as_ref().map(|_| exit_action_tx.clone());
            tokio::spawn(async move {
                run_selected_candidate_file_selector(
                    file_cfg,
                    file_hot,
                    file_curve_cache,
                    file_shred_curve_replay,
                    file_exit_watcher,
                    file_exit_actions,
                )
                .await;
            });
        }
    }

    if cfg.selected_candidate_sniper.enabled
        && cfg.selected_candidate_sniper.submit_enabled
        && cfg.selected_candidate_sniper.wallet_reconcile_enabled
    {
        let reconcile_cfg = cfg.selected_candidate_sniper.clone();
        let reconcile_hot = cfg.hot_sniper.clone();
        let reconcile_registry = exit_watcher.clone();
        let reconcile_exit_actions = exit_watcher.as_ref().map(|_| exit_action_tx.clone());
        tokio::spawn(async move {
            run_selected_candidate_wallet_reconciler(
                reconcile_cfg,
                reconcile_hot,
                reconcile_registry,
                reconcile_exit_actions,
            )
            .await;
        });
        eprintln!(
            "rust-sidecar: selected candidate wallet reconciler ON every {}ms auto_flatten={}",
            cfg.selected_candidate_sniper.wallet_reconcile_interval_ms,
            cfg.selected_candidate_sniper
                .wallet_reconcile_auto_flatten_enabled
        );
    }

    if cfg.exit_watcher_enabled && cfg.yellowstone_enabled {
        let curve_cfg = cfg.clone();
        let curve_registry = exit_watcher.clone().expect("exit watcher registry");
        let curve_actions = exit_action_tx.clone();
        if parse_env_bool("RUST_EXIT_CURVE_ACCOUNT_DYNAMIC_STREAM_ENABLED", true) {
            tokio::spawn(async move {
                loop {
                    if let Err(err) = run_exit_curve_account_dynamic_stream(
                        &curve_cfg,
                        &curve_registry,
                        &curve_actions,
                    )
                    .await
                    {
                        eprintln!(
                            "rust-sidecar: dynamic exit curve-account stream error: {err:#}; reconnecting in {}ms",
                            curve_cfg.reconnect_ms
                        );
                        sleep(Duration::from_millis(curve_cfg.reconnect_ms)).await;
                    }
                }
            });
            eprintln!("rust-sidecar: dynamic exact exit curve-account stream ON");
        } else {
            // Legacy mode reconnects when the active curve set changes. Keep it
            // available as a fallback for providers that reject dynamic request
            // updates, but the warmed dynamic stream is lower latency.
            tokio::spawn(async move {
                loop {
                    if let Err(err) =
                        run_exit_curve_account_stream(&curve_cfg, &curve_registry, &curve_actions)
                            .await
                    {
                        eprintln!(
                            "rust-sidecar: exit curve-account stream error: {err:#}; reconnecting in {}ms",
                            curve_cfg.reconnect_ms
                        );
                        sleep(Duration::from_millis(curve_cfg.reconnect_ms)).await;
                    }
                }
            });
            eprintln!("rust-sidecar: exact exit curve-account stream ON");
        }
    }

    if cfg.exit_watcher_enabled && cfg.exit_poll_fallback_enabled {
        let poll_cfg = cfg.clone();
        let poll_registry = exit_watcher.clone().expect("exit watcher registry");
        let poll_actions = exit_action_tx.clone();
        tokio::spawn(async move {
            run_exit_curve_poll_fallback(&poll_cfg, &poll_registry, &poll_actions).await;
        });
        eprintln!(
            "rust-sidecar: exit curve RPC poll fallback ON every {}ms",
            cfg.exit_poll_fallback_ms
        );
    }

    if cfg.selected_candidate_sniper.enabled
        && cfg.selected_candidate_sniper.curve_cache_enabled
        && cfg.yellowstone_enabled
    {
        let curve_endpoints = selected_candidate_curve_grpc_endpoints(&cfg);
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateCurveCacheEndpointRaceStarted",
                "atMs": now_ms(),
                "endpointCount": curve_endpoints.len(),
                "endpoints": curve_endpoints,
            }),
        );
        for endpoint in selected_candidate_curve_grpc_endpoints(&cfg) {
            let cache_cfg = cfg.clone();
            let cache_exit_watcher = exit_watcher.clone();
            let cache_exit_actions = cache_exit_watcher.as_ref().map(|_| exit_action_tx.clone());
            tokio::spawn(async move {
                loop {
                    if let Err(err) = run_selected_candidate_curve_cache_stream(
                        &cache_cfg,
                        &endpoint,
                        cache_exit_watcher.as_ref(),
                        cache_exit_actions.as_ref(),
                    )
                    .await
                    {
                        eprintln!(
                            "rust-sidecar: selected candidate curve-cache stream endpoint={endpoint} error: {err:#}; reconnecting in {}ms",
                            cache_cfg.reconnect_ms
                        );
                        sleep(Duration::from_millis(cache_cfg.reconnect_ms)).await;
                    }
                }
            });
        }
        eprintln!("rust-sidecar: selected candidate curve-cache endpoint race ON");
    }

    if cfg.selected_candidate_sniper.enabled
        && cfg.selected_candidate_sniper.curve_cache_enabled
        && cfg
            .selected_candidate_sniper
            .reserve_observation_source
            .is_some()
    {
        let reserve_cfg = cfg.clone();
        let reserve_exit_watcher = exit_watcher.clone();
        let reserve_exit_actions = reserve_exit_watcher
            .as_ref()
            .map(|_| exit_action_tx.clone());
        tokio::spawn(async move {
            run_selected_candidate_reserve_observation_file_cache(
                &reserve_cfg,
                reserve_exit_watcher.as_ref(),
                reserve_exit_actions.as_ref(),
            )
            .await;
        });
        eprintln!(
            "rust-sidecar: selected candidate reserve-observation file cache ON source={}",
            cfg.selected_candidate_sniper
                .reserve_observation_source
                .as_deref()
                .unwrap_or("")
        );
    }

    if cfg.selected_candidate_sniper.enabled
        && cfg.selected_candidate_sniper.early_balance_activate_enabled
        && cfg.selected_candidate_sniper.balance_cache_enabled
        && cfg.yellowstone_enabled
    {
        let balance_endpoints = selected_candidate_balance_grpc_endpoints(&cfg);
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateBalanceProofEndpointRaceStarted",
                "atMs": now_ms(),
                "endpointCount": balance_endpoints.len(),
                "endpoints": balance_endpoints,
                "streams": {
                    "ataBalanceCache": true,
                    "walletOwnerBalanceCache": cfg
                        .selected_candidate_sniper
                        .balance_cache_wallet_owner_stream_enabled,
                },
            }),
        );
        for endpoint in selected_candidate_balance_grpc_endpoints(&cfg) {
            let balance_cfg = cfg.clone();
            let balance_endpoint = endpoint.clone();
            let balance_exit_watcher = exit_watcher.clone();
            let balance_exit_actions = balance_exit_watcher
                .as_ref()
                .map(|_| exit_action_tx.clone());
            tokio::spawn(async move {
                loop {
                    if let Err(err) = run_selected_candidate_balance_cache_stream_at_endpoint(
                        &balance_cfg,
                        &balance_endpoint,
                        balance_exit_watcher.as_ref(),
                        balance_exit_actions.as_ref(),
                    )
                    .await
                    {
                        eprintln!(
                            "rust-sidecar: selected candidate balance-cache stream endpoint={} error: {err:#}; reconnecting in {}ms",
                            balance_endpoint,
                            balance_cfg.reconnect_ms
                        );
                        sleep(Duration::from_millis(balance_cfg.reconnect_ms)).await;
                    }
                }
            });
            eprintln!(
                "rust-sidecar: selected candidate ATA balance-cache stream ON endpoint={endpoint}"
            );
        }
        if cfg
            .selected_candidate_sniper
            .balance_cache_wallet_owner_stream_enabled
        {
            for endpoint in selected_candidate_balance_grpc_endpoints(&cfg) {
                let wallet_balance_cfg = cfg.clone();
                let wallet_balance_endpoint = endpoint.clone();
                let wallet_balance_exit_watcher = exit_watcher.clone();
                let wallet_balance_exit_actions = wallet_balance_exit_watcher
                    .as_ref()
                    .map(|_| exit_action_tx.clone());
                tokio::spawn(async move {
                    loop {
                        if let Err(err) =
                            run_selected_candidate_wallet_balance_cache_stream_at_endpoint(
                                &wallet_balance_cfg,
                                &wallet_balance_endpoint,
                                wallet_balance_exit_watcher.as_ref(),
                                wallet_balance_exit_actions.as_ref(),
                            )
                            .await
                        {
                            eprintln!(
                                "rust-sidecar: selected candidate wallet-owner balance-cache stream endpoint={} error: {err:#}; reconnecting in {}ms",
                                wallet_balance_endpoint,
                                wallet_balance_cfg.reconnect_ms
                            );
                            sleep(Duration::from_millis(wallet_balance_cfg.reconnect_ms)).await;
                        }
                    }
                });
                eprintln!(
                    "rust-sidecar: selected candidate wallet-owner balance-cache stream ON endpoint={endpoint}"
                );
            }
        }
    }

    if cfg.jito_shredstream_enabled {
        #[cfg(feature = "jito-shredstream")]
        {
            let jito_cfg = cfg.clone();
            let jito_exit_watcher = exit_watcher.clone();
            let jito_exit_actions = exit_watcher.as_ref().map(|_| exit_action_tx.clone());
            tokio::spawn(async move {
                loop {
                    if let Err(err) = run_jito_shredstream_once(
                        &jito_cfg,
                        jito_exit_watcher.as_ref(),
                        jito_exit_actions.as_ref(),
                    )
                    .await
                    {
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
            bail!(
                "JITO_SHREDSTREAM_ENABLED=true but this binary was built without --features jito-shredstream"
            );
        }
    }

    if !cfg.yellowstone_enabled {
        futures::future::pending::<()>().await;
        return Ok(());
    }

    loop {
        if let Err(err) = run_once(
            &cfg,
            exit_watcher.as_ref(),
            exit_watcher.as_ref().map(|_| &exit_action_tx),
        )
        .await
        {
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
        let mut hot_sniper = HotSniperConfig::from_env();
        let selected_candidate_sniper = SelectedCandidateSniperConfig::from_env();
        let selected_candidate_curve_cache = CandidateCurveCache::default();
        let selected_candidate_shred_curve_replay = SelectedCandidateShredCurveReplay::default();
        let native_strict_selector = NativeStrictSelectorConfig::from_env();
        validate_selected_candidate_execution_fee_policy(&hot_sniper, &selected_candidate_sniper)?;
        validate_selected_candidate_sell_fee_policy(&hot_sniper, &selected_candidate_sniper)?;
        if let Some(reason) = native_strict_submit_parity_config_error(
            selected_candidate_sniper.submit_enabled,
            native_strict_selector.enabled,
            native_strict_selector.parity_candidate_source.as_deref(),
            native_strict_selector
                .comparison_candidate_source
                .as_deref(),
            selected_candidate_sniper.candidate_source.as_deref(),
        ) {
            bail!("{reason}");
        }
        if selected_candidate_sniper.enabled
            && parse_env_bool("RUST_SELECTED_CANDIDATE_DISABLE_HOT_SNIPER", true)
        {
            hot_sniper.enabled = false;
        }
        let exit_watcher_enabled = (parse_env_bool("RUST_EXIT_WATCHER_ENABLED", false)
            || (selected_candidate_sniper.enabled && selected_candidate_sniper.submit_enabled))
            && !parse_env_bool("SIMULATION_MODE", false)
            && !parse_env_bool("SHADOW_EXECUTION_MODE", false);
        if exit_watcher_enabled && !yellowstone_enabled {
            bail!("RUST_EXIT_WATCHER_ENABLED=true requires Yellowstone account subscriptions for local executable curve marks");
        }
        if exit_watcher_enabled && !parse_env_bool("RUST_EXECUTOR_ENABLED", false) {
            bail!("RUST_EXIT_WATCHER_ENABLED=true requires RUST_EXECUTOR_ENABLED=true for the local sell command channel");
        }
        let exit_subscription_refresh_ms =
            parse_env_u64("RUST_EXIT_SUBSCRIPTION_REFRESH_MS", 25).clamp(10, 1_000);
        let exit_poll_fallback_enabled = parse_env_bool("RUST_EXIT_POLL_FALLBACK_ENABLED", true);
        let exit_poll_fallback_ms =
            parse_env_u64("RUST_EXIT_POLL_FALLBACK_MS", 250).clamp(50, 5_000);
        let exit_poll_rpc_timeout_ms =
            parse_env_u64("RUST_EXIT_POLL_RPC_TIMEOUT_MS", 80).clamp(25, 1_000);
        Ok(Self {
            yellowstone_enabled,
            endpoint,
            x_token,
            jito_shredstream_enabled,
            jito_shredstream_grpc_url,
            tracked_wallets,
            reconnect_ms,
            exit_watcher_enabled,
            exit_subscription_refresh_ms,
            exit_poll_fallback_enabled,
            exit_poll_fallback_ms,
            exit_poll_rpc_timeout_ms,
            hot_sniper,
            selected_candidate_sniper,
            selected_candidate_curve_cache,
            selected_candidate_shred_curve_replay,
            native_strict_selector,
        })
    }
}

fn validate_selected_candidate_execution_fee_policy(
    hot: &HotSniperConfig,
    selected: &SelectedCandidateSniperConfig,
) -> Result<()> {
    if !selected.enabled
        || !selected.submit_enabled
        || !parse_env_bool("RUST_SELECTED_CANDIDATE_ENFORCE_ZERO_EXECUTION_FEES", false)
    {
        return Ok(());
    }
    let (priority_fee_micro_lamports, jito_tip_lamports, helius_tip_lamports) =
        selected_candidate_buy_fees(hot, selected.buy_lamports.max(1));
    if priority_fee_micro_lamports != 0 || jito_tip_lamports != 0 || helius_tip_lamports != 0 {
        bail!(
            "RUST_SELECTED_CANDIDATE_ENFORCE_ZERO_EXECUTION_FEES=true but selected buy fees resolved nonzero: priority={} jito_tip={} helius_tip={}",
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports
        );
    }
    validate_selected_candidate_sell_fee_policy(hot, selected)?;
    Ok(())
}

fn validate_selected_candidate_sell_fee_policy(
    hot: &HotSniperConfig,
    selected: &SelectedCandidateSniperConfig,
) -> Result<()> {
    if !selected.enabled
        || !selected.submit_enabled
        || !parse_env_bool("RUST_SELECTED_CANDIDATE_ENFORCE_ZERO_SELL_FEES", false)
    {
        return Ok(());
    }
    if parse_env_bool("RUST_SELECTED_CANDIDATE_SELL_USE_HOT_FEE_PROFILE", true) {
        bail!("RUST_SELECTED_CANDIDATE_ENFORCE_ZERO_SELL_FEES=true requires RUST_SELECTED_CANDIDATE_SELL_USE_HOT_FEE_PROFILE=false");
    }
    let (priority_fee_micro_lamports, jito_tip_lamports, helius_tip_lamports) =
        selected_candidate_sell_fees(hot, selected.buy_lamports.max(1));
    if priority_fee_micro_lamports != 0 || jito_tip_lamports != 0 || helius_tip_lamports != 0 {
        bail!(
            "RUST_SELECTED_CANDIDATE_ENFORCE_ZERO_SELL_FEES=true but selected sell fees resolved nonzero: priority={} jito_tip={} helius_tip={}",
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports
        );
    }
    Ok(())
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
        eprintln!(
            "rust-sidecar: hot sniper fees priority={} microLamports/CU jito_tip={} helius_tip={} cap={} pct={}",
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
            parse_env_optional_u64("RUST_HOT_SNIPER_MAX_EXECUTION_FEE_LAMPORTS")
                .or_else(|| optional_sol_env_lamports_nonnegative("RUST_HOT_SNIPER_MAX_EXECUTION_FEE_SOL"))
                .or_else(|| optional_sol_env_lamports_nonnegative("MAX_BUY_EXECUTION_FEE_SOL"))
                .map(|v| v.to_string())
                .unwrap_or_else(|| "off".to_string()),
            parse_env_optional_nonnegative_f64("RUST_HOT_SNIPER_MAX_FEE_PCT_OF_BUY")
                .or_else(|| parse_env_optional_nonnegative_f64("MAX_BUY_EXECUTION_FEE_PCT_OF_BUY"))
                .map(|v| v.to_string())
                .unwrap_or_else(|| "off".to_string())
        );
        let configured_regions = configured_jito_regions();
        eprintln!(
            "{}",
            json!({
                "kind": "rustJitoRegionsConfigured",
                "regions": configured_regions,
                "source": configured_jito_regions_source(),
                "sendMode": env::var("JITO_SEND_MODE").unwrap_or_else(|_| "bundle".to_string()),
            })
        );
        let max_execution_fee_lamports =
            parse_env_optional_u64("RUST_HOT_SNIPER_MAX_EXECUTION_FEE_LAMPORTS")
                .or_else(|| {
                    optional_sol_env_lamports_nonnegative("RUST_HOT_SNIPER_MAX_EXECUTION_FEE_SOL")
                })
                .or_else(|| optional_sol_env_lamports_nonnegative("MAX_BUY_EXECUTION_FEE_SOL"));
        let max_execution_fee_pct_of_buy =
            parse_env_optional_nonnegative_f64("RUST_HOT_SNIPER_MAX_FEE_PCT_OF_BUY")
                .or_else(|| parse_env_optional_nonnegative_f64("MAX_BUY_EXECUTION_FEE_PCT_OF_BUY"));
        Self {
            enabled,
            buy_once_per_token: parse_env_bool("BUY_ONCE_PER_TOKEN", true),
            bought_mints: Arc::new(Mutex::new(HashSet::new())),
            bought_signatures: Arc::new(Mutex::new(HashSet::new())),
            session_deployed_lamports: Arc::new(AtomicU64::new(0)),
            blockhash_cache: Arc::new(Mutex::new(None)),
            max_session_deployed_lamports: optional_sol_env_lamports("MAX_SESSION_DEPLOYED_SOL"),
            min_market_cap_usd: parse_env_optional_f64("PREBUY_MIN_MARKET_CAP_USD"),
            max_market_cap_usd: parse_env_optional_f64("PREBUY_MAX_MARKET_CAP_USD"),
            min_market_cap_sol: parse_env_optional_f64("LOCAL_PUMPFUN_MIN_MARKET_CAP_SOL"),
            max_market_cap_sol: parse_env_optional_f64("LOCAL_PUMPFUN_MAX_MARKET_CAP_SOL"),
            sol_price_usd: parse_env_optional_f64("LOCAL_PUMPFUN_SOL_PRICE_USD")
                .or_else(|| parse_env_optional_f64("SOL_PRICE_USD")),
            market_cap_filter_enabled: parse_env_bool(
                "LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED",
                false,
            ),
            market_cap_filter_fail_open: parse_env_bool("PREBUY_FILTER_FAIL_OPEN", false),
            market_cap_rpc_timeout_ms: parse_env_u64("RUST_HOT_SNIPER_MC_RPC_TIMEOUT_MS", 80)
                .clamp(25, 500),
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
            exact_sol_in: parse_env_bool("PUMPFUN_EXACT_SOL_IN_BUYS", true),
            compute_unit_limit: parse_env_u64("COMPUTE_UNIT_LIMIT", 160_000).min(1_400_000) as u32,
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
            max_execution_fee_lamports,
            max_execution_fee_pct_of_buy,
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

impl SelectedCandidateSniperConfig {
    fn from_env() -> Self {
        let enabled = parse_env_bool("RUST_SELECTED_CANDIDATE_SNIPER_ENABLED", false)
            && !parse_env_bool("SIMULATION_MODE", false)
            && !parse_env_bool("SHADOW_EXECUTION_MODE", false);
        let submit_enabled = parse_env_bool("RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED", false);
        let no_submit_dry_build_enabled =
            parse_env_bool("RUST_SELECTED_CANDIDATE_NO_SUBMIT_DRY_BUILD_ENABLED", false);
        let bind_host = env::var("RUST_SELECTED_CANDIDATE_TCP_HOST")
            .unwrap_or_else(|_| "127.0.0.1".to_string());
        let bind_port =
            parse_env_u64("RUST_SELECTED_CANDIDATE_TCP_PORT", 0).min(u16::MAX as u64) as u16;
        let buy_lamports = optional_sol_env_lamports("RUST_SELECTED_CANDIDATE_BUY_SOL")
            .or_else(|| optional_sol_env_lamports("EDGE_LIVE_BUY_SOL"))
            .unwrap_or(2_000_000)
            .max(1);
        let max_entry_drift_bps =
            parse_env_optional_u64("RUST_SELECTED_CANDIDATE_MAX_ENTRY_DRIFT_BPS")
                .or_else(|| {
                    parse_env_optional_f64("EDGE_LIVE_DYNAMIC_ENTRY_MAX_DRIFT_PCT")
                        .map(|pct| (pct * 100.0).floor().max(0.0) as u64)
                })
                .unwrap_or(1_000);
        let selected_event_fallback_max_entry_drift_bps = parse_env_u64(
            "RUST_SELECTED_CANDIDATE_SELECTED_EVENT_FALLBACK_MAX_ENTRY_DRIFT_BPS",
            max_entry_drift_bps,
        )
        .clamp(max_entry_drift_bps, 5_000);
        let landing_buffer_bps =
            parse_env_u64("RUST_SELECTED_CANDIDATE_LANDING_BUFFER_BPS", 250).min(9_500);
        let selected_event_fallback_landing_buffer_bps = parse_env_u64(
            "RUST_SELECTED_CANDIDATE_SELECTED_EVENT_FALLBACK_LANDING_BUFFER_BPS",
            landing_buffer_bps,
        )
        .min(9_500);
        let fee_quote_haircut_bps =
            parse_env_u64("RUST_SELECTED_CANDIDATE_FEE_QUOTE_HAIRCUT_BPS", 0).min(2_000);
        let max_min_out_below_protected_bps =
            parse_env_u64("RUST_SELECTED_CANDIDATE_MAX_MIN_OUT_BELOW_PROTECTED_BPS", 0).min(9_500);
        let buy_output_policy = parse_selected_candidate_buy_output_policy();
        Self {
            enabled,
            submit_enabled,
            no_submit_dry_build_enabled,
            bind_host,
            bind_port,
            required_lane: env_option_string("RUST_SELECTED_CANDIDATE_REQUIRED_SHADOW_LANE")
                .or_else(|| env_option_string("TWO_CQ_IMMEDIATE_DUST_REQUIRED_SHADOW_LANE")),
            allowed_lanes: env_csv_strings("RUST_SELECTED_CANDIDATE_ALLOWED_SHADOW_LANES"),
            required_policy: env_option_string("RUST_SELECTED_CANDIDATE_REQUIRED_SHADOW_POLICY")
                .or_else(|| env_option_string("TWO_CQ_IMMEDIATE_DUST_REQUIRED_SHADOW_POLICY")),
            max_event_lag_ms: parse_env_optional_u64("RUST_SELECTED_CANDIDATE_MAX_EVENT_LAG_MS")
                .or_else(|| parse_env_optional_u64("TWO_CQ_IMMEDIATE_DUST_MAX_EVENT_LAG_MS"))
                .unwrap_or(1_500),
            max_session_buys: parse_env_optional_u64("RUST_SELECTED_CANDIDATE_MAX_SESSION_BUYS")
                .or_else(|| parse_env_optional_u64("EDGE_LIVE_MAX_SESSION_BUYS"))
                .or_else(|| parse_env_optional_u64("TWO_CQ_IMMEDIATE_DUST_MAX_SESSION_EXECUTIONS"))
                .unwrap_or(1),
            max_open_positions: parse_env_optional_u64("RUST_SELECTED_CANDIDATE_MAX_OPEN")
                .or_else(|| parse_env_optional_u64("EDGE_LIVE_MAX_OPEN"))
                .unwrap_or(1),
            buy_lamports,
            min_entry_mc_usd: parse_env_optional_f64("RUST_SELECTED_CANDIDATE_MIN_ENTRY_MC_USD")
                .or_else(|| parse_env_optional_f64("TWO_CQ_IMMEDIATE_DUST_MIN_ENTRY_MC_USD")),
            max_entry_mc_usd: parse_env_optional_f64("RUST_SELECTED_CANDIDATE_MAX_ENTRY_MC_USD")
                .or_else(|| parse_env_optional_f64("TWO_CQ_IMMEDIATE_DUST_MAX_ENTRY_MC_USD")),
            max_entry_drift_bps,
            selected_event_fallback_max_entry_drift_bps,
            landing_buffer_bps,
            selected_event_fallback_landing_buffer_bps,
            fee_quote_haircut_bps,
            max_min_out_below_protected_bps,
            buy_output_policy,
            max_entry_market_cap_usd: parse_env_optional_f64(
                "RUST_SELECTED_CANDIDATE_MAX_ENTRY_MC_AFTER_DRIFT_USD",
            )
            .or_else(|| parse_env_optional_f64("EDGE_LIVE_DYNAMIC_ENTRY_MAX_MC_USD")),
            post_buy_reject_fill_below_protected_min: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_POST_BUY_REJECT_FILL_BELOW_PROTECTED_MIN",
                false,
            ),
            reject_mayhem_retry_promoted: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_REJECT_MAYHEM_RETRY_PROMOTED",
                true,
            ),
            reject_weak_mayhem_recovered: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_REJECT_WEAK_MAYHEM_RECOVERED",
                true,
            ),
            audit_file: env_option_string("RUST_SELECTED_CANDIDATE_AUDIT_FILE"),
            candidate_source: env_option_string("RUST_SELECTED_CANDIDATE_SOURCE"),
            candidate_start_at_end: parse_env_bool("RUST_SELECTED_CANDIDATE_START_AT_END", true),
            candidate_poll_ms: parse_env_u64("RUST_SELECTED_CANDIDATE_POLL_MS", 50)
                .clamp(10, 1_000),
            candidate_offset: Arc::new(AtomicU64::new(0)),
            curve_cache_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_CURVE_CACHE_ENABLED",
                true,
            ),
            curve_cache_owner_wide: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_CURVE_CACHE_OWNER_WIDE",
                false,
            ),
            curve_cache_require_independent: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_CURVE_CACHE_REQUIRE_INDEPENDENT",
                false,
            ),
            curve_cache_wait_ms: parse_env_u64("RUST_SELECTED_CANDIDATE_CURVE_CACHE_WAIT_MS", 25)
                .min(250),
            curve_cache_max_age_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_CURVE_CACHE_MAX_AGE_MS",
                250,
            )
            .clamp(10, 2_000),
            reserve_observation_source: env_option_string(
                "RUST_SELECTED_CANDIDATE_RESERVE_OBSERVATION_SOURCE",
            ),
            reserve_observation_start_at_end: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_RESERVE_OBSERVATION_START_AT_END",
                true,
            ),
            reserve_observation_poll_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_RESERVE_OBSERVATION_POLL_MS",
                10,
            )
            .clamp(5, 1_000),
            min_send_time_mc_drift_pct: parse_env_optional_f64(
                "RUST_SELECTED_CANDIDATE_MIN_SEND_TIME_MC_DRIFT_PCT",
            ),
            max_send_time_mc_drift_pct: parse_env_optional_f64(
                "RUST_SELECTED_CANDIDATE_MAX_SEND_TIME_MC_DRIFT_PCT",
            ),
            assumed_shadow_exit_roi_pct: parse_env_optional_f64(
                "RUST_SELECTED_CANDIDATE_ASSUMED_SHADOW_EXIT_ROI_PCT",
            ),
            min_live_entry_adjusted_roi_pct: parse_env_optional_f64(
                "RUST_SELECTED_CANDIDATE_MIN_LIVE_ENTRY_ADJUSTED_ROI_PCT",
            ),
            curve_rpc_fallback_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_CURVE_RPC_FALLBACK_ENABLED",
                true,
            ),
            optimistic_exit_activate_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_OPTIMISTIC_EXIT_ACTIVATE_ENABLED",
                false,
            ),
            early_balance_activate_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_EARLY_BALANCE_ACTIVATE_ENABLED",
                false,
            ),
            early_balance_timeout_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EARLY_BALANCE_TIMEOUT_MS",
                0,
            )
            .min(1_000),
            early_balance_rpc_fallback_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_EARLY_BALANCE_RPC_FALLBACK_ENABLED",
                false,
            ),
            exact_balance_proof_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_EXACT_BALANCE_PROOF_ENABLED",
                true,
            ),
            exact_balance_proof_timeout_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EXACT_BALANCE_PROOF_TIMEOUT_MS",
                2_000,
            )
            .clamp(100, 10_000),
            exact_balance_proof_poll_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EXACT_BALANCE_PROOF_POLL_MS",
                25,
            )
            .clamp(5, 250),
            exact_balance_proof_rpc_timeout_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EXACT_BALANCE_PROOF_RPC_TIMEOUT_MS",
                80,
            )
            .clamp(25, 500),
            direct_ata_balance_probe_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_DIRECT_ATA_BALANCE_PROBE_ENABLED",
                true,
            ),
            direct_ata_balance_probe_per_candidate_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_DIRECT_ATA_BALANCE_PROBE_PER_CANDIDATE_ENABLED",
                false,
            ),
            allow_pre_proof_sell_broadcast: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_ALLOW_PRE_PROOF_SELL_BROADCAST",
                false,
            ) && parse_env_bool(
                "RUST_SELECTED_CANDIDATE_UNSAFE_PRE_PROOF_SELL_BROADCAST_ENABLED",
                false,
            ),
            pre_proof_sell_require_local_landed: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_PRE_PROOF_SELL_REQUIRE_LOCAL_LANDED",
                true,
            ),
            landed_safe_exit_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_LANDED_SAFE_EXIT_ENABLED",
                false,
            ),
            landed_safe_exit_timeout_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_LANDED_SAFE_EXIT_TIMEOUT_MS",
                400,
            )
            .clamp(50, 2_000),
            landed_safe_exit_poll_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_LANDED_SAFE_EXIT_POLL_MS",
                10,
            )
            .clamp(5, 250),
            landed_safe_exit_history_fallback_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_LANDED_SAFE_EXIT_HISTORY_FALLBACK_ENABLED",
                false,
            ),
            landed_safe_exit_rpc_fallback_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_LANDED_SAFE_EXIT_RPC_FALLBACK_ENABLED",
                false,
            ),
            guaranteed_min_out_exit_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_GUARANTEED_MIN_OUT_EXIT_ENABLED",
                false,
            ),
            guaranteed_min_out_exit_bps: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_GUARANTEED_MIN_OUT_EXIT_BPS",
                9_800,
            )
            .clamp(1, 10_000),
            balance_cache_prebuy_watch_settle_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_PREBUY_WATCH_SETTLE_MS",
                35,
            )
            .min(500),
            balance_cache_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_ENABLED",
                true,
            ),
            balance_cache_wallet_owner_stream_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_WALLET_OWNER_STREAM_ENABLED",
                false,
            ),
            balance_cache_wait_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_WAIT_MS",
                350,
            )
            .clamp(25, 1_000),
            balance_cache_max_age_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_MAX_AGE_MS",
                2_000,
            )
            .clamp(25, 10_000),
            balance_cache_rpc_fallback_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_RPC_FALLBACK_ENABLED",
                true,
            ),
            balance_cache: SelectedCandidateBalanceCache::default(),
            wallet_reconcile_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_WALLET_RECONCILE_ENABLED",
                true,
            ),
            wallet_reconcile_auto_flatten_enabled: parse_env_bool(
                "RUST_SELECTED_CANDIDATE_WALLET_RECONCILE_AUTO_FLATTEN_ENABLED",
                false,
            ),
            wallet_reconcile_interval_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_WALLET_RECONCILE_INTERVAL_MS",
                15_000,
            )
            .clamp(2_000, 300_000),
            wallet_reconcile_unmanaged_grace_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_WALLET_RECONCILE_UNMANAGED_GRACE_MS",
                60_000,
            )
            .clamp(5_000, 3_600_000),
            wallet_reconcile_seen: Arc::new(Mutex::new(HashMap::new())),
            pending_exact_proof_zero_since: Arc::new(Mutex::new(HashMap::new())),
            forwarded_keys: Arc::new(Mutex::new(HashSet::new())),
            active_mints: Arc::new(Mutex::new(HashSet::new())),
            session_attempts: Arc::new(AtomicU64::new(0)),
        }
    }
}

fn parse_selected_candidate_buy_output_policy() -> String {
    let raw = env::var("RUST_SELECTED_CANDIDATE_BUY_OUTPUT_POLICY")
        .ok()
        .or_else(|| env::var("RUST_SELECTED_CANDIDATE_BUY_FILL_POLICY").ok())
        .unwrap_or_else(|| "normal_slippage".to_string());
    match raw.trim().to_ascii_lowercase().as_str() {
        "fill_first" | "fillfirst" | "fill-first" => "fill_first".to_string(),
        "aggressive_slippage" | "aggressive" | "aggressive-slippage" => {
            "aggressive_slippage".to_string()
        }
        _ => "normal_slippage".to_string(),
    }
}

fn selected_candidate_fill_first_buy_policy(cfg: &SelectedCandidateSniperConfig) -> bool {
    cfg.buy_output_policy == "fill_first"
}

impl NativeStrictSelectorConfig {
    fn from_env() -> Self {
        Self {
            enabled: parse_env_bool("RUST_NATIVE_STRICT_SELECTOR_ENABLED", false)
                && !parse_env_bool("SIMULATION_MODE", false)
                && !parse_env_bool("SHADOW_EXECUTION_MODE", false),
            state: Arc::new(Mutex::new(NativeStrictState::default())),
            parity_candidates: Arc::new(Mutex::new(HashMap::new())),
            parity_candidate_source: env_option_string(
                "RUST_NATIVE_STRICT_SELECTOR_PARITY_CANDIDATE_SOURCE",
            ),
            comparison_candidate_source: env_option_string(
                "RUST_NATIVE_STRICT_SELECTOR_COMPARISON_CANDIDATE_SOURCE",
            ),
            parity_candidate_start_at_end: parse_env_bool(
                "RUST_NATIVE_STRICT_SELECTOR_PARITY_START_AT_END",
                false,
            ),
            parity_max_age_ms: parse_env_u64(
                "RUST_NATIVE_STRICT_SELECTOR_PARITY_MAX_AGE_MS",
                2_000,
            )
            .clamp(250, 10_000),
            parity_wait_ms: parse_env_u64("RUST_NATIVE_STRICT_SELECTOR_PARITY_WAIT_MS", 0)
                .clamp(0, 2_000),
            feature_sol_price_usd: parse_env_optional_f64(
                "RUST_NATIVE_STRICT_SELECTOR_FEATURE_SOL_PRICE_USD",
            )
            .or_else(|| parse_env_optional_f64("SHADOW_FEATURE_SOL_PRICE_USD"))
            .unwrap_or(160.0),
            min_feature_mc_usd: positive_f64_or_default(
                parse_env_optional_f64("RUST_NATIVE_STRICT_SELECTOR_MIN_FEATURE_MC_USD"),
                5_000.0,
            ),
            max_feature_mc_usd: positive_f64_or_default(
                parse_env_optional_f64("RUST_NATIVE_STRICT_SELECTOR_MAX_FEATURE_MC_USD"),
                8_000.0,
            ),
            source_template_cache_miss_fallback: parse_env_bool(
                "RUST_NATIVE_STRICT_SELECTOR_SOURCE_TEMPLATE_CACHE_MISS_FALLBACK",
                false,
            ),
            max_mints: parse_env_u64("RUST_NATIVE_STRICT_SELECTOR_MAX_MINTS", 8192)
                .clamp(128, 100_000) as usize,
            mint_ttl_ms: parse_env_u64("RUST_NATIVE_STRICT_SELECTOR_MINT_TTL_MS", 120_000)
                .clamp(10_000, 900_000),
            status_interval_ms: parse_env_u64(
                "RUST_NATIVE_STRICT_SELECTOR_STATUS_INTERVAL_MS",
                30_000,
            )
            .clamp(5_000, 300_000),
        }
    }
}

fn env_option_string(name: &str) -> Option<String> {
    env::var(name).ok().and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn env_csv_strings(name: &str) -> Vec<String> {
    env::var(name)
        .unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect()
}

fn native_strict_submit_parity_config_error(
    submit_enabled: bool,
    native_strict_enabled: bool,
    parity_candidate_source: Option<&str>,
    comparison_candidate_source: Option<&str>,
    selected_candidate_source: Option<&str>,
) -> Option<&'static str> {
    if !submit_enabled || !native_strict_enabled || selected_candidate_source.is_some() {
        return None;
    }
    if parity_candidate_source.is_some() {
        return None;
    }
    if comparison_candidate_source.is_some() {
        return Some("RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED=true with RUST_NATIVE_STRICT_SELECTOR_ENABLED=true cannot use only RUST_NATIVE_STRICT_SELECTOR_COMPARISON_CANDIDATE_SOURCE; set RUST_NATIVE_STRICT_SELECTOR_PARITY_CANDIDATE_SOURCE so shadow_miss candidates are fail-closed before buy");
    }
    Some("RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED=true with RUST_NATIVE_STRICT_SELECTOR_ENABLED=true requires RUST_NATIVE_STRICT_SELECTOR_PARITY_CANDIDATE_SOURCE for exact-shadow live submit")
}

fn positive_f64_or_default(value: Option<f64>, fallback: f64) -> Option<f64> {
    let selected = value.unwrap_or(fallback);
    selected
        .is_finite()
        .then_some(selected)
        .filter(|v| *v > 0.0)
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
    env::var(name).ok().and_then(|v| {
        let trimmed = v.trim();
        if trimmed.is_empty() {
            None
        } else {
            trimmed.parse::<u64>().ok()
        }
    })
}

fn parse_env_f64(name: &str, fallback: f64) -> f64 {
    env::var(name)
        .ok()
        .and_then(|v| v.trim().parse::<f64>().ok())
        .filter(|v| v.is_finite())
        .unwrap_or(fallback)
}

fn parse_env_optional_f64(name: &str) -> Option<f64> {
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
}

fn parse_env_optional_nonnegative_f64(name: &str) -> Option<f64> {
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
        .filter(|v| v.is_finite() && *v >= 0.0)
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

fn optional_sol_env_lamports_nonnegative(name: &str) -> Option<u64> {
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
        .filter(|v| v.is_finite() && *v >= 0.0)
        .map(|v| (v * LAMPORTS_PER_SOL).round() as u64)
}

fn capped_hot_fees(hot: &HotSniperConfig, sol_in_lamports: u64) -> (u64, u64, u64) {
    let priority_fee_micro_lamports = hot.priority_fee_micro_lamports;
    let jito_tip_lamports = hot.jito_tip_lamports;
    let helius_tip_lamports = hot.helius_tip_lamports;
    cap_execution_fees(
        priority_fee_micro_lamports,
        jito_tip_lamports,
        helius_tip_lamports,
        hot.compute_unit_limit,
        hot_execution_fee_caps(hot, sol_in_lamports),
    )
}

fn hot_execution_fee_caps(hot: &HotSniperConfig, sol_in_lamports: u64) -> Vec<u64> {
    let mut caps = Vec::new();
    if let Some(cap) = hot.max_execution_fee_lamports {
        caps.push(cap);
    }
    if let Some(pct) = hot.max_execution_fee_pct_of_buy {
        caps.push(((sol_in_lamports as f64) * (pct / 100.0)).floor().max(0.0) as u64);
    }
    caps
}

fn selected_candidate_override_execution_fee_caps(sol_in_lamports: u64) -> Vec<u64> {
    let mut caps = Vec::new();
    if let Some(cap) = parse_env_optional_u64("RUST_SELECTED_CANDIDATE_MAX_EXECUTION_FEE_LAMPORTS")
    {
        caps.push(cap);
    }
    if let Some(cap) =
        optional_sol_env_lamports_nonnegative("RUST_SELECTED_CANDIDATE_MAX_EXECUTION_FEE_SOL")
    {
        caps.push(cap);
    }
    if let Some(pct) =
        parse_env_optional_nonnegative_f64("RUST_SELECTED_CANDIDATE_MAX_FEE_PCT_OF_BUY")
    {
        caps.push(((sol_in_lamports as f64) * (pct / 100.0)).floor().max(0.0) as u64);
    }
    caps
}

fn cap_execution_fees(
    mut priority_fee_micro_lamports: u64,
    mut jito_tip_lamports: u64,
    mut helius_tip_lamports: u64,
    compute_unit_limit: u32,
    caps: Vec<u64>,
) -> (u64, u64, u64) {
    let Some(cap_lamports) = caps.into_iter().min() else {
        return (
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
        );
    };
    let estimate = estimated_fee_lamports(
        priority_fee_micro_lamports,
        jito_tip_lamports,
        helius_tip_lamports,
        compute_unit_limit,
    );
    if estimate <= cap_lamports {
        return (
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
        );
    }
    let scale = (cap_lamports as f64 / estimate.max(1) as f64).clamp(0.0, 1.0);
    priority_fee_micro_lamports = ((priority_fee_micro_lamports as f64) * scale).floor() as u64;
    jito_tip_lamports = ((jito_tip_lamports as f64) * scale).floor() as u64;
    helius_tip_lamports = ((helius_tip_lamports as f64) * scale).floor() as u64;
    while estimated_fee_lamports(
        priority_fee_micro_lamports,
        jito_tip_lamports,
        helius_tip_lamports,
        compute_unit_limit,
    ) > cap_lamports
        && priority_fee_micro_lamports > 0
    {
        priority_fee_micro_lamports -= 1;
    }
    (
        priority_fee_micro_lamports,
        jito_tip_lamports,
        helius_tip_lamports,
    )
}

fn selected_candidate_buy_fees(hot: &HotSniperConfig, buy_lamports: u64) -> (u64, u64, u64) {
    let force_zero = parse_env_bool("RUST_SELECTED_CANDIDATE_ENFORCE_ZERO_EXECUTION_FEES", false);
    let use_hot_profile = parse_env_bool(
        "RUST_SELECTED_CANDIDATE_BUY_USE_HOT_FEE_PROFILE",
        !force_zero,
    );
    let base = if use_hot_profile && !force_zero {
        capped_hot_fees(hot, buy_lamports.max(1))
    } else {
        (0, 0, 0)
    };
    let priority_fee_micro_lamports =
        parse_env_optional_u64("RUST_SELECTED_CANDIDATE_BUY_PRIORITY_FEE_MICRO_LAMPORTS")
            .or_else(|| parse_env_optional_u64("BUY_PRIORITY_FEE_MICRO_LAMPORTS"))
            .unwrap_or(base.0);
    let jito_tip_lamports = parse_env_optional_u64("RUST_SELECTED_CANDIDATE_BUY_JITO_TIP_LAMPORTS")
        .or_else(|| parse_env_optional_u64("RUST_SELECTED_CANDIDATE_BUY_TIP_LAMPORTS"))
        .or_else(|| parse_env_optional_u64("BUY_JITO_TIP_LAMPORTS"))
        .unwrap_or(base.1);
    let helius_tip_lamports =
        parse_env_optional_u64("RUST_SELECTED_CANDIDATE_BUY_HELIUS_TIP_LAMPORTS")
            .or_else(|| parse_env_optional_u64("BUY_HELIUS_TIP_LAMPORTS"))
            .unwrap_or(base.2);
    if parse_env_bool("RUST_SELECTED_CANDIDATE_CAP_FEE_OVERRIDES_ENABLED", false) {
        cap_execution_fees(
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
            hot.compute_unit_limit,
            selected_candidate_override_execution_fee_caps(buy_lamports.max(1)),
        )
    } else {
        (
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
        )
    }
}

fn selected_candidate_sell_fees(
    hot: &HotSniperConfig,
    position_value_lamports: u64,
) -> (u64, u64, u64) {
    let base = if parse_env_bool("RUST_SELECTED_CANDIDATE_SELL_USE_HOT_FEE_PROFILE", true) {
        capped_hot_fees(hot, position_value_lamports.max(1))
    } else {
        (0, 0, 0)
    };
    let priority_fee_micro_lamports =
        parse_env_optional_u64("RUST_SELECTED_CANDIDATE_SELL_PRIORITY_FEE_MICRO_LAMPORTS")
            .unwrap_or(base.0);
    let jito_tip_lamports =
        parse_env_optional_u64("RUST_SELECTED_CANDIDATE_SELL_JITO_TIP_LAMPORTS")
            .or_else(|| parse_env_optional_u64("RUST_SELECTED_CANDIDATE_SELL_TIP_LAMPORTS"))
            .unwrap_or(base.1);
    let helius_tip_lamports =
        parse_env_optional_u64("RUST_SELECTED_CANDIDATE_SELL_HELIUS_TIP_LAMPORTS")
            .unwrap_or(base.2);
    if parse_env_bool("RUST_SELECTED_CANDIDATE_CAP_FEE_OVERRIDES_ENABLED", false) {
        cap_execution_fees(
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
            hot.compute_unit_limit,
            selected_candidate_override_execution_fee_caps(position_value_lamports.max(1)),
        )
    } else {
        (
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
        )
    }
}

fn estimated_fee_lamports(
    priority_fee_micro_lamports: u64,
    jito_tip_lamports: u64,
    helius_tip_lamports: u64,
    compute_unit_limit: u32,
) -> u64 {
    let units = u64::from(compute_unit_limit.min(1_400_000).max(1));
    let priority_lamports = priority_fee_micro_lamports
        .saturating_mul(units)
        .saturating_add(999_999)
        / 1_000_000;
    priority_lamports
        .saturating_add(jito_tip_lamports)
        .saturating_add(helius_tip_lamports)
}

fn selected_candidate_exit_decision_fee_lamports(
    estimated_execution_fee_lamports: u64,
    cost_basis_lamports: u64,
) -> u64 {
    let mode = env::var("RUST_SELECTED_CANDIDATE_EXIT_DECISION_FEE_MODE")
        .unwrap_or_else(|_| "actual".to_string())
        .trim()
        .to_ascii_lowercase();
    match mode.as_str() {
        "gross" | "zero" | "none" | "shadow" => 0,
        "fixed" => parse_env_u64(
            "RUST_SELECTED_CANDIDATE_EXIT_DECISION_FEE_LAMPORTS",
            estimated_execution_fee_lamports,
        ),
        "production" | "production_normalized" | "production-normalized" | "normalized" => {
            let production_lamports =
                parse_env_optional_u64("RUST_SELECTED_CANDIDATE_EXIT_DECISION_PRODUCTION_LAMPORTS")
                    .unwrap_or_else(|| {
                        sol_env_lamports(
                            "RUST_SELECTED_CANDIDATE_EXIT_DECISION_PRODUCTION_SOL",
                            1.0,
                        )
                    })
                    .max(1);
            ((estimated_execution_fee_lamports as u128 * cost_basis_lamports.max(1) as u128)
                / production_lamports as u128)
                .min(estimated_execution_fee_lamports as u128) as u64
        }
        _ => estimated_execution_fee_lamports,
    }
}

fn configured_jito_regions() -> Vec<String> {
    let raw = env::var("RUST_JITO_REGIONS_OVERRIDE")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .or_else(|| env::var("JITO_REGIONS").ok())
        .unwrap_or_default();
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

fn configured_jito_regions_source() -> &'static str {
    if env::var("RUST_JITO_REGIONS_OVERRIDE")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .is_some()
    {
        "RUST_JITO_REGIONS_OVERRIDE"
    } else if env::var("JITO_REGIONS")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .is_some()
    {
        "JITO_REGIONS"
    } else if env::var("JITO_REGION")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .is_some()
    {
        "JITO_REGION"
    } else {
        "default-mainnet"
    }
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

async fn run_broadcast_prewarm_loop(hot: HotSniperConfig) {
    let interval_ms =
        parse_env_u64("RUST_BROADCAST_PREWARM_INTERVAL_MS", 15_000).clamp(1_000, 120_000);
    eprintln!(
        "{}",
        json!({
            "kind": "rustBroadcastPrewarmStarted",
            "intervalMs": interval_ms,
            "jitoSendMode": hot.jito_send_mode,
            "jitoTransactionUrlCount": hot.jito_transaction_urls.len(),
            "jitoBundleUrlCount": hot.jito_bundle_urls.len(),
            "useJitoDirect": hot.use_jito_direct,
            "useJitoFallback": hot.use_jito_fallback,
            "senderRpcFallbackEnabled": hot.sender_rpc_fallback_enabled,
            "senderEndpointConfigured": !hot.sender_endpoint_url.trim().is_empty(),
            "rpcFallbackConfigured": !hot.rpc_fallback_url.trim().is_empty(),
        })
    );
    loop {
        let prewarm_cfg = executor::BroadcastPrewarmConfig {
            use_jito_direct: hot.use_jito_direct,
            use_jito_fallback: hot.use_jito_fallback,
            sender_rpc_fallback_enabled: hot.sender_rpc_fallback_enabled,
            jito_send_mode: hot.jito_send_mode.clone(),
            jito_transaction_urls: hot.jito_transaction_urls.clone(),
            jito_bundle_urls: hot.jito_bundle_urls.clone(),
            sender_endpoint_url: hot.sender_endpoint_url.clone(),
            rpc_fallback_url: hot.rpc_fallback_url.clone(),
            jito_auth_uuid: hot.jito_auth_uuid.clone(),
        };
        if let Err(err) = executor::prewarm_broadcast_connections(prewarm_cfg).await {
            eprintln!("rust-sidecar: broadcast prewarm failed: {err:#}");
        }
        sleep(Duration::from_millis(interval_ms)).await;
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

#[derive(Debug)]
struct SignatureStatusCheck {
    status: &'static str,
    ok: Option<bool>,
    confirmation_status: Option<String>,
    slot: Option<u64>,
    error: Option<String>,
}

async fn confirm_signature_status(
    rpc_url: &str,
    signature: &str,
    timeout_ms: u64,
    poll_ms: u64,
) -> SignatureStatusCheck {
    confirm_signature_status_with_history(rpc_url, signature, timeout_ms, poll_ms, true).await
}

async fn confirm_signature_status_with_history(
    rpc_url: &str,
    signature: &str,
    timeout_ms: u64,
    poll_ms: u64,
    search_transaction_history: bool,
) -> SignatureStatusCheck {
    let started = Instant::now();
    let timeout_ms = timeout_ms.clamp(50, 10_000);
    let poll_ms = poll_ms.clamp(5, 1_000);
    loop {
        match fetch_signature_status_with_history(rpc_url, signature, search_transaction_history)
            .await
        {
            Ok(Some(status)) => return status,
            Ok(None) => {}
            Err(err) => {
                return SignatureStatusCheck {
                    status: "status_rpc_error",
                    ok: None,
                    confirmation_status: None,
                    slot: None,
                    error: Some(err.to_string()),
                }
            }
        }
        if started.elapsed().as_millis() >= timeout_ms as u128 {
            return SignatureStatusCheck {
                status: "status_timeout",
                ok: None,
                confirmation_status: None,
                slot: None,
                error: None,
            };
        }
        sleep(Duration::from_millis(poll_ms)).await;
    }
}

async fn fetch_signature_status(
    rpc_url: &str,
    signature: &str,
) -> Result<Option<SignatureStatusCheck>> {
    fetch_signature_status_with_history(rpc_url, signature, true).await
}

async fn fetch_signature_status_with_history(
    rpc_url: &str,
    signature: &str,
    search_transaction_history: bool,
) -> Result<Option<SignatureStatusCheck>> {
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignatureStatuses",
        "params": [[signature], { "searchTransactionHistory": search_transaction_history }],
    });
    let response = reqwest::Client::new()
        .post(rpc_url)
        .timeout(Duration::from_millis(900))
        .json(&body)
        .send()
        .await
        .context("signature status rpc send failed")?;
    let json = response
        .json::<JsonRpcResponse>()
        .await
        .context("signature status rpc response parse failed")?;
    if let Some(error) = json.error {
        let code = error.code.map(|c| format!(" code {c}")).unwrap_or_default();
        bail!(
            "{}{}",
            error
                .message
                .unwrap_or_else(|| "getSignatureStatuses json-rpc error".to_string()),
            code
        );
    }
    let Some(value) = json
        .result
        .and_then(|result| result.get("value").cloned())
        .and_then(|value| value.as_array().and_then(|arr| arr.first().cloned()))
    else {
        return Ok(None);
    };
    if value.is_null() {
        return Ok(None);
    }
    let confirmation_status = value
        .get("confirmationStatus")
        .and_then(Value::as_str)
        .map(str::to_string);
    let slot = value.get("slot").and_then(Value::as_u64);
    let err = value.get("err").filter(|err| !err.is_null()).cloned();
    if let Some(err) = err {
        return Ok(Some(SignatureStatusCheck {
            status: "finalized_error",
            ok: Some(false),
            confirmation_status,
            slot,
            error: Some(err.to_string()),
        }));
    }
    if matches!(
        confirmation_status.as_deref(),
        Some("processed" | "confirmed" | "finalized")
    ) {
        return Ok(Some(SignatureStatusCheck {
            status: "landed_ok",
            ok: Some(true),
            confirmation_status,
            slot,
            error: None,
        }));
    }
    Ok(None)
}

async fn fetch_selected_candidate_token_balance_raw(
    rpc_url: &str,
    mint: &str,
    token_program_id: Option<&str>,
    timeout_ms: u64,
) -> Result<Option<u64>> {
    let timeout_ms = timeout_ms.max(1);
    let token_program_id = Pubkey::from_str(token_program_id.unwrap_or(TOKEN_2022_PROGRAM_ID))
        .context("post-buy balance token program invalid")?;
    let mint = Pubkey::from_str(mint).context("post-buy balance mint invalid")?;
    let owner = selected_candidate_wallet_pubkey()?;
    let ata = derive_associated_token_address_main(&owner, &mint, &token_program_id)?;
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountBalance",
        "params": [ata.to_string(), { "commitment": "processed" }],
    });
    let client = selected_candidate_rpc_client();
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    loop {
        let remaining_ms = deadline
            .saturating_duration_since(Instant::now())
            .as_millis()
            .max(1) as u64;
        let response = match client
            .post(rpc_url)
            .timeout(Duration::from_millis(remaining_ms.min(80)))
            .json(&body)
            .send()
            .await
        {
            Ok(response) => response,
            Err(err) => {
                if Instant::now() >= deadline {
                    return Err(err).context("post-buy token balance rpc send failed");
                }
                sleep(Duration::from_millis(20)).await;
                continue;
            }
        };
        let json = match response.json::<JsonRpcResponse>().await {
            Ok(json) => json,
            Err(err) => {
                if Instant::now() >= deadline {
                    return Err(err).context("post-buy token balance rpc response parse failed");
                }
                sleep(Duration::from_millis(20)).await;
                continue;
            }
        };
        if let Some(error) = json.error {
            let message = error.message.unwrap_or_default();
            if message
                .to_ascii_lowercase()
                .contains("could not find account")
            {
                if Instant::now() >= deadline {
                    return Ok(None);
                }
                sleep(Duration::from_millis(25)).await;
                continue;
            }
            let code = error.code.map(|c| format!(" code {c}")).unwrap_or_default();
            bail!(
                "{}{}",
                if message.is_empty() {
                    "getTokenAccountBalance json-rpc error".to_string()
                } else {
                    message
                },
                code
            );
        }
        return Ok(json
            .result
            .and_then(|result| result.get("value").cloned())
            .and_then(|value| {
                value
                    .get("amount")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .and_then(|amount| amount.parse::<u64>().ok()));
    }
}

static SELECTED_CANDIDATE_RPC_CLIENT: OnceLock<reqwest::Client> = OnceLock::new();

fn selected_candidate_rpc_client() -> &'static reqwest::Client {
    SELECTED_CANDIDATE_RPC_CLIENT.get_or_init(reqwest::Client::new)
}

async fn wait_selected_candidate_cached_token_balance(
    cfg: &SelectedCandidateSniperConfig,
    candidate: &SelectedCandidate,
    timeout_ms: u64,
) -> Option<CachedTokenAccountBalance> {
    wait_selected_candidate_cached_token_balance_with_exact(cfg, candidate, timeout_ms, false).await
}

async fn wait_selected_candidate_cached_exact_token_balance(
    cfg: &SelectedCandidateSniperConfig,
    candidate: &SelectedCandidate,
    timeout_ms: u64,
) -> Option<CachedTokenAccountBalance> {
    wait_selected_candidate_cached_token_balance_with_exact(cfg, candidate, timeout_ms, true).await
}

async fn wait_selected_candidate_cached_token_balance_with_exact(
    cfg: &SelectedCandidateSniperConfig,
    candidate: &SelectedCandidate,
    timeout_ms: u64,
    require_exact: bool,
) -> Option<CachedTokenAccountBalance> {
    let timeout_ms = timeout_ms.max(1);
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    loop {
        let cached = if require_exact {
            cfg.balance_cache
                .get_fresh_exact_for_candidate(candidate, cfg.balance_cache_max_age_ms)
        } else {
            cfg.balance_cache
                .get_fresh_for_candidate(candidate, cfg.balance_cache_max_age_ms)
        };
        if let Some(cached) = cached {
            return Some(cached);
        }
        if Instant::now() >= deadline {
            return None;
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        let notified = cfg.balance_cache.notify.notified();
        if timeout(remaining, notified).await.is_err() {
            return None;
        }
    }
}

async fn run_selected_candidate_early_balance_activator(
    cfg: SelectedCandidateSniperConfig,
    hot: HotSniperConfig,
    registry: exit_watcher::ExitWatcherRegistry,
    action_tx: UnboundedSender<exit_watcher::ExitAction>,
    candidate: SelectedCandidate,
    quoted_tokens_out_raw: u64,
    economic_tokens_held_raw: u64,
    sol_in_lamports: u64,
    activated: Arc<AtomicBool>,
    balance_proved: Arc<AtomicBool>,
) {
    let early_started = Instant::now();
    let cache_wait_ms = if cfg.balance_cache_enabled {
        cfg.early_balance_timeout_ms
    } else {
        0
    };
    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateEarlyBalanceActivatorStarted",
            "atMs": now_ms(),
            "key": candidate.key,
            "mint": candidate.mint,
            "cacheWaitMs": cache_wait_ms,
            "timeoutMs": cfg.early_balance_timeout_ms,
            "rpcFallbackEnabled": cfg.early_balance_rpc_fallback_enabled,
            "note": "confirmation is audit/reconciliation; local balance proof can activate exit first",
        }),
    );

    let cached_balance = if cache_wait_ms > 0 {
        wait_selected_candidate_cached_token_balance(&cfg, &candidate, cache_wait_ms).await
    } else {
        None
    };
    let mut early_balance_source = "yellowstone-token-balance-cache";
    let early_balance = if let Some(cached) = cached_balance {
        emit_selected_candidate_sniper_event(
            &cfg,
            json!({
                "kind": "rustSelectedCandidateEarlyBalanceCacheHit",
                "atMs": now_ms(),
                "key": candidate.key,
                "mint": candidate.mint,
                "ata": cached.ata,
                "slot": cached.slot,
                "observedAtMs": cached.observed_at_ms,
                "amountRaw": cached.amount_raw,
                "exact": cached.exact,
                "balanceSource": cached.source,
                "proofClass": if cached.exact {
                    "exact_token_account_balance"
                } else {
                    "local_submitted_buy_signature"
                },
                "reconciliationRequired": !cached.exact,
                "elapsedMs": early_started.elapsed().as_millis(),
            }),
        );
        Ok(Some(cached))
    } else if cfg.early_balance_rpc_fallback_enabled {
        early_balance_source = "rpc-getTokenAccountBalance";
        let elapsed_ms = early_started.elapsed().as_millis().min(u64::MAX as u128) as u64;
        let remaining_ms = cfg
            .early_balance_timeout_ms
            .saturating_sub(elapsed_ms)
            .max(1);
        fetch_selected_candidate_token_balance_raw(
            &hot.rpc_fallback_url,
            &candidate.mint,
            candidate.token_program_id.as_deref(),
            remaining_ms,
        )
        .await
        .map(|balance| {
            balance.and_then(|amount_raw| {
                let (ata, token_program_id) = selected_candidate_ata_for_mint(
                    &candidate.mint,
                    candidate.token_program_id.as_deref(),
                )
                .ok()?;
                Some(CachedTokenAccountBalance {
                    ata,
                    mint: candidate.mint.clone(),
                    owner: selected_candidate_wallet_pubkey().ok()?.to_string(),
                    token_program_id,
                    amount_raw,
                    slot: 0,
                    observed_at_ms: now_ms().min(u64::MAX as u128) as u64,
                    exact: true,
                    source: "rpc-getTokenAccountBalance",
                })
            })
        })
    } else {
        Ok(None)
    };

    match early_balance {
        Ok(Some(cached)) if cached.amount_raw > 0 && !cached.exact => {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateEarlyBalanceDeferred",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "quotedTokensOutRaw": quoted_tokens_out_raw,
                    "economicTokensHeldRaw": economic_tokens_held_raw,
                    "observedTokensOutRaw": cached.amount_raw,
                    "balanceSource": cached.source,
                    "exact": cached.exact,
                    "proofClass": "local_submitted_buy_signature",
                    "inventoryUpdated": false,
                    "activationAllowed": false,
                    "reconciliationRequired": true,
                    "elapsedMs": early_started.elapsed().as_millis(),
                    "activationMode": "deferred_until_exact_balance_proof",
                    "reason": "non_exact_balance_proof_cannot_arm_exit",
                }),
            );
        }
        Ok(Some(cached)) if cached.amount_raw > 0 => {
            let actual_tokens_out_raw = cached.amount_raw;
            early_balance_source = cached.source;
            let update_status = registry.update(exit_watcher::UpdatePumpfunExitWatchCommand {
                id: format!(
                    "rust-selected-early-balance-{}-{}",
                    now_ms(),
                    candidate.mint
                ),
                mint: candidate.mint.clone(),
                tokens_held_raw: actual_tokens_out_raw,
                cost_basis_lamports: sol_in_lamports,
                exact_proof: cached.exact,
                proof_source: Some(cached.source.to_string()),
            });
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateEarlyBalanceUpdate",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "quotedTokensOutRaw": quoted_tokens_out_raw,
                    "economicTokensHeldRaw": economic_tokens_held_raw,
                    "actualTokensOutRaw": actual_tokens_out_raw,
                    "actualFillDegradationPct": selected_candidate_actual_fill_degradation_pct(
                        economic_tokens_held_raw,
                        actual_tokens_out_raw,
                    ),
                    "impliedActualEntryMarketCapUsd": selected_candidate_implied_actual_entry_mc_usd(
                        Some(candidate.entry_market_cap_usd),
                        economic_tokens_held_raw,
                        actual_tokens_out_raw,
                    ),
                    "balanceSource": early_balance_source,
                    "exact": cached.exact,
                    "proofClass": if cached.exact {
                        "exact_token_account_balance"
                    } else {
                        "local_submitted_buy_signature"
                    },
                    "reconciliationRequired": false,
                    "inventoryUpdated": true,
                    "activationAllowed": true,
                    "elapsedMs": early_started.elapsed().as_millis(),
                    "ok": update_status.ok,
                    "phase": update_status.phase,
                    "error": update_status.error,
                    "activationMode": "pre_confirmation_balance_proof",
                }),
            );
            if update_status.ok {
                let activate_status =
                    activate_selected_candidate_exit_watch(&cfg, &registry, &action_tx, &candidate);
                if activate_status.ok {
                    balance_proved.store(true, Ordering::SeqCst);
                    activated.store(true, Ordering::SeqCst);
                } else {
                    emit_selected_candidate_sniper_event(
                        &cfg,
                        json!({
                            "kind": "rustSelectedCandidateExitWatchActivationFailed",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "source": "early_balance",
                            "phase": activate_status.phase,
                            "error": activate_status.error,
                        }),
                    );
                }
            }
        }
        Ok(Some(_)) | Ok(None) => {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateEarlyBalanceMissing",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "timeoutMs": cfg.early_balance_timeout_ms,
                    "cacheWaitMs": cache_wait_ms,
                    "rpcFallbackEnabled": cfg.early_balance_rpc_fallback_enabled,
                    "elapsedMs": early_started.elapsed().as_millis(),
                    "activationMode": "pre_confirmation_balance_proof",
                }),
            );
        }
        Err(err) => {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateEarlyBalanceError",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "timeoutMs": cfg.early_balance_timeout_ms,
                    "cacheWaitMs": cache_wait_ms,
                    "balanceSource": early_balance_source,
                    "elapsedMs": early_started.elapsed().as_millis(),
                    "error": err.to_string(),
                    "activationMode": "pre_confirmation_balance_proof",
                }),
            );
        }
    }
}

async fn run_selected_candidate_landed_safe_exit_activator(
    cfg: SelectedCandidateSniperConfig,
    hot: HotSniperConfig,
    registry: exit_watcher::ExitWatcherRegistry,
    action_tx: UnboundedSender<exit_watcher::ExitAction>,
    candidate: SelectedCandidate,
    signature: String,
    quoted_tokens_out_raw: u64,
    safe_sell_tokens_raw: u64,
    sol_in_lamports: u64,
    buy_result_at_ms: u128,
    selected_started: Instant,
    activated: Arc<AtomicBool>,
    exact_balance_proved: Arc<AtomicBool>,
) {
    let started = Instant::now();
    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateLandedSafeExitStarted",
            "atMs": now_ms(),
            "key": candidate.key,
            "mint": candidate.mint,
            "signature": signature,
            "quotedTokensOutRaw": quoted_tokens_out_raw,
            "safeSellTokensRaw": safe_sell_tokens_raw,
            "timeoutMs": cfg.landed_safe_exit_timeout_ms,
            "pollMs": cfg.landed_safe_exit_poll_ms,
            "historyFallbackEnabled": cfg.landed_safe_exit_history_fallback_enabled,
            "rpcFallbackEnabled": cfg.landed_safe_exit_rpc_fallback_enabled,
            "primaryProofSource": "yellowstone-submitted-buy-transaction",
            "activationMode": "landed_signature_safe_amount",
        }),
    );

    if exact_balance_proved.load(Ordering::SeqCst) {
        emit_selected_candidate_sniper_event(
            &cfg,
            json!({
                "kind": "rustSelectedCandidateLandedSafeExitSkipped",
                "atMs": now_ms(),
                "key": candidate.key,
                "mint": candidate.mint,
                "signature": signature,
                "reason": "exact_balance_already_proved",
                "elapsedMs": started.elapsed().as_millis(),
            }),
        );
        return;
    }

    let local_landed = wait_selected_candidate_cached_token_balance(
        &cfg,
        &candidate,
        cfg.landed_safe_exit_timeout_ms,
    )
    .await;
    let local_elapsed_ms = started.elapsed().as_millis();
    let mut landed_status = "status_timeout".to_string();
    let mut landed_ok: Option<bool> = None;
    let mut landed_confirmation_status: Option<String> = None;
    let mut landed_slot: Option<u64> = None;
    let mut landed_error: Option<String> = None;
    let mut status_source = "yellowstone-submitted-buy-transaction".to_string();

    if let Some(cached) = local_landed {
        if cached.exact {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateLandedSafeExitSkipped",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "signature": signature,
                    "reason": "exact_balance_won_local_stream_race",
                    "statusSource": cached.source,
                    "slot": cached.slot,
                    "observedAtMs": cached.observed_at_ms,
                    "elapsedMs": started.elapsed().as_millis(),
                }),
            );
            return;
        }
        landed_status = "landed_ok".to_string();
        landed_ok = Some(true);
        landed_confirmation_status = Some("processed-local-stream".to_string());
        landed_slot = Some(cached.slot);
        status_source = cached.source.to_string();
    } else if cfg.landed_safe_exit_rpc_fallback_enabled {
        let mut landed = confirm_signature_status_with_history(
            &hot.rpc_fallback_url,
            &signature,
            cfg.landed_safe_exit_timeout_ms,
            cfg.landed_safe_exit_poll_ms,
            false,
        )
        .await;
        status_source = "getSignatureStatuses-fresh".to_string();
        if landed.ok.is_none() && cfg.landed_safe_exit_history_fallback_enabled {
            landed = confirm_signature_status_with_history(
                &hot.rpc_fallback_url,
                &signature,
                cfg.landed_safe_exit_timeout_ms,
                cfg.landed_safe_exit_poll_ms,
                true,
            )
            .await;
            status_source = "getSignatureStatuses-history-fallback".to_string();
        }
        landed_status = landed.status.to_string();
        landed_ok = landed.ok;
        landed_confirmation_status = landed.confirmation_status;
        landed_slot = landed.slot;
        landed_error = landed.error;
    }

    if landed_ok != Some(true) {
        emit_selected_candidate_sniper_event(
            &cfg,
            json!({
                "kind": "rustSelectedCandidateLandedSafeExitNotReady",
                "atMs": now_ms(),
                "key": candidate.key,
                "mint": candidate.mint,
                "signature": signature,
                "status": landed_status,
                "confirmationStatus": landed_confirmation_status,
                "slot": landed_slot,
                "error": landed_error,
                "statusSource": status_source,
                "localStreamElapsedMs": local_elapsed_ms,
                "elapsedMs": started.elapsed().as_millis(),
                "activationAllowed": false,
                "reason": if landed_ok == Some(false) {
                    "buy_landed_failed"
                } else {
                    "local_buy_landed_not_proved_before_timeout"
                },
            }),
        );
        return;
    }

    if exact_balance_proved.load(Ordering::SeqCst) {
        emit_selected_candidate_sniper_event(
            &cfg,
            json!({
                "kind": "rustSelectedCandidateLandedSafeExitSkipped",
                "atMs": now_ms(),
                "key": candidate.key,
                "mint": candidate.mint,
                "signature": signature,
                "reason": "exact_balance_won_race",
                "statusSource": status_source,
                "elapsedMs": started.elapsed().as_millis(),
            }),
        );
        return;
    }

    let update_status = registry.update(exit_watcher::UpdatePumpfunExitWatchCommand {
        id: format!(
            "rust-selected-landed-safe-balance-{}-{}",
            now_ms(),
            candidate.mint
        ),
        mint: candidate.mint.clone(),
        tokens_held_raw: safe_sell_tokens_raw,
        cost_basis_lamports: sol_in_lamports,
        exact_proof: false,
        proof_source: Some(status_source.to_string()),
    });
    let mut activation_ok = false;
    if update_status.ok && !activated.load(Ordering::SeqCst) {
        let activate_status =
            activate_selected_candidate_exit_watch(&cfg, &registry, &action_tx, &candidate);
        activation_ok = activate_status.ok;
        if activation_ok {
            activated.store(true, Ordering::SeqCst);
        } else {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateExitWatchActivationFailed",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "source": "landed_safe_exit",
                    "phase": activate_status.phase,
                    "error": activate_status.error,
                }),
            );
        }
    }

    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateLandedSafeExitActivated",
            "atMs": now_ms(),
            "key": candidate.key,
            "mint": candidate.mint,
            "signature": signature,
            "status": landed_status,
            "confirmationStatus": landed_confirmation_status,
            "slot": landed_slot,
            "statusSource": status_source,
            "quotedTokensOutRaw": quoted_tokens_out_raw,
            "safeSellTokensRaw": safe_sell_tokens_raw,
            "actualTokensOutRaw": safe_sell_tokens_raw,
            "exact": false,
            "inventoryUpdated": update_status.ok,
            "activationAllowed": update_status.ok,
            "activationOk": activation_ok,
            "phase": update_status.phase,
            "error": update_status.error,
            "proofClass": "landed_signature_safe_amount",
            "reconciliationRequired": true,
            "elapsedMs": started.elapsed().as_millis(),
            "buyResultToLandedSafeMs": now_ms().saturating_sub(buy_result_at_ms),
            "rustAcceptedToFirstSellableMs": selected_started.elapsed().as_millis(),
            "eventLagMs": candidate.event_lag_ms,
            "activationMode": "landed_signature_safe_amount",
        }),
    );
}

async fn run_selected_candidate_exact_balance_prover(
    cfg: SelectedCandidateSniperConfig,
    hot: HotSniperConfig,
    registry: exit_watcher::ExitWatcherRegistry,
    action_tx: UnboundedSender<exit_watcher::ExitAction>,
    candidate: SelectedCandidate,
    quoted_tokens_out_raw: u64,
    sol_in_lamports: u64,
    activated: Arc<AtomicBool>,
    balance_proved: Arc<AtomicBool>,
) {
    let started = Instant::now();
    let timeout_ms = cfg.exact_balance_proof_timeout_ms.max(1);
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    let mut local_tx_proof_reported = false;
    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateExactBalanceProofStarted",
            "atMs": now_ms(),
            "key": candidate.key,
            "mint": candidate.mint,
            "timeoutMs": timeout_ms,
            "pollMs": cfg.exact_balance_proof_poll_ms,
            "rpcFallbackEnabled": cfg.balance_cache_rpc_fallback_enabled,
            "rpcTimeoutMs": cfg.exact_balance_proof_rpc_timeout_ms,
        }),
    );

    loop {
        if balance_proved.load(Ordering::SeqCst) {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateExactBalanceProofSkipped",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "reason": "already_proved",
                    "elapsedMs": started.elapsed().as_millis(),
                }),
            );
            return;
        }

        if cfg.balance_cache_enabled {
            if let Some(cached) =
                wait_selected_candidate_cached_exact_token_balance(&cfg, &candidate, 1).await
            {
                if cached.amount_raw > 0 {
                    balance_proved.store(true, Ordering::SeqCst);
                    let status = registry.update(exit_watcher::UpdatePumpfunExitWatchCommand {
                        id: format!(
                            "rust-selected-exact-cache-balance-{}-{}",
                            now_ms(),
                            candidate.mint
                        ),
                        mint: candidate.mint.clone(),
                        tokens_held_raw: cached.amount_raw,
                        cost_basis_lamports: sol_in_lamports,
                        exact_proof: true,
                        proof_source: Some(cached.source.to_string()),
                    });
                    let mut activation_ok = false;
                    if status.ok {
                        let activate_status = activate_selected_candidate_exit_watch(
                            &cfg, &registry, &action_tx, &candidate,
                        );
                        activation_ok = activate_status.ok;
                        if activation_ok {
                            activated.store(true, Ordering::SeqCst);
                        } else {
                            emit_selected_candidate_sniper_event(
                                &cfg,
                                json!({
                                    "kind": "rustSelectedCandidateExitWatchActivationFailed",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "source": "exact_cache_balance",
                                    "phase": activate_status.phase,
                                    "error": activate_status.error,
                                }),
                            );
                        }
                    }
                    emit_selected_candidate_sniper_event(
                        &cfg,
                        json!({
                            "kind": "rustSelectedCandidateExactBalanceProof",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "ata": cached.ata,
                            "slot": cached.slot,
                            "observedAtMs": cached.observed_at_ms,
                            "quotedTokensOutRaw": quoted_tokens_out_raw,
                            "actualTokensOutRaw": cached.amount_raw,
                            "actualFillDegradationPct": selected_candidate_actual_fill_degradation_pct(
                                quoted_tokens_out_raw,
                                cached.amount_raw,
                            ),
                            "impliedActualEntryMarketCapUsd": selected_candidate_implied_actual_entry_mc_usd(
                                Some(candidate.entry_market_cap_usd),
                                quoted_tokens_out_raw,
                                cached.amount_raw,
                            ),
                            "exact": true,
                            "balanceSource": cached.source,
                            "elapsedMs": started.elapsed().as_millis(),
                            "ok": status.ok,
                            "phase": status.phase,
                            "error": status.error,
                            "inventoryUpdated": status.ok,
                            "activationAllowed": status.ok,
                            "activationOk": activation_ok,
                            "activationMode": "exact_balance_proof_cache",
                        }),
                    );
                    return;
                }
            }
            if let Some(cached) =
                wait_selected_candidate_cached_token_balance(&cfg, &candidate, 1).await
            {
                if cached.amount_raw > 0
                    && cached.source == "yellowstone-successful-submitted-buy-signature"
                    && !local_tx_proof_reported
                {
                    emit_selected_candidate_sniper_event(
                        &cfg,
                        json!({
                            "kind": "rustSelectedCandidateLocalBalanceProof",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "ata": cached.ata,
                            "slot": cached.slot,
                            "observedAtMs": cached.observed_at_ms,
                            "quotedTokensOutRaw": quoted_tokens_out_raw,
                            "actualTokensOutRaw": cached.amount_raw,
                            "proofKind": "successful_processed_tx_non_exact",
                            "exact": false,
                            "balanceSource": cached.source,
                            "elapsedMs": started.elapsed().as_millis(),
                            "ok": true,
                            "inventoryUpdated": false,
                            "activationAllowed": false,
                            "reconciliationRequired": true,
                            "reason": "non_exact_submitted_buy_proof_cannot_update_sell_inventory",
                        }),
                    );
                    local_tx_proof_reported = true;
                }
            }
        }

        if cfg.balance_cache_rpc_fallback_enabled {
            match fetch_selected_candidate_token_balance_raw(
                &hot.rpc_fallback_url,
                &candidate.mint,
                candidate.token_program_id.as_deref(),
                cfg.exact_balance_proof_rpc_timeout_ms,
            )
            .await
            {
                Ok(Some(amount_raw)) if amount_raw > 0 => {
                    balance_proved.store(true, Ordering::SeqCst);
                    let status = registry.update(exit_watcher::UpdatePumpfunExitWatchCommand {
                        id: format!(
                            "rust-selected-exact-rpc-balance-{}-{}",
                            now_ms(),
                            candidate.mint
                        ),
                        mint: candidate.mint.clone(),
                        tokens_held_raw: amount_raw,
                        cost_basis_lamports: sol_in_lamports,
                        exact_proof: true,
                        proof_source: Some("rpc-getTokenAccountBalance".to_string()),
                    });
                    let mut activation_ok = false;
                    if status.ok {
                        let activate_status = activate_selected_candidate_exit_watch(
                            &cfg, &registry, &action_tx, &candidate,
                        );
                        activation_ok = activate_status.ok;
                        if activation_ok {
                            balance_proved.store(true, Ordering::SeqCst);
                            activated.store(true, Ordering::SeqCst);
                        } else {
                            emit_selected_candidate_sniper_event(
                                &cfg,
                                json!({
                                    "kind": "rustSelectedCandidateExitWatchActivationFailed",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "source": "exact_rpc_balance",
                                    "phase": activate_status.phase,
                                    "error": activate_status.error,
                                }),
                            );
                        }
                    }
                    emit_selected_candidate_sniper_event(
                        &cfg,
                        json!({
                            "kind": "rustSelectedCandidateExactBalanceProof",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "quotedTokensOutRaw": quoted_tokens_out_raw,
                            "actualTokensOutRaw": amount_raw,
                            "actualFillDegradationPct": selected_candidate_actual_fill_degradation_pct(
                                quoted_tokens_out_raw,
                                amount_raw,
                            ),
                            "impliedActualEntryMarketCapUsd": selected_candidate_implied_actual_entry_mc_usd(
                                Some(candidate.entry_market_cap_usd),
                                quoted_tokens_out_raw,
                                amount_raw,
                            ),
                            "exact": true,
                            "balanceSource": "rpc-getTokenAccountBalance",
                            "elapsedMs": started.elapsed().as_millis(),
                            "ok": status.ok,
                            "phase": status.phase,
                            "error": status.error,
                            "inventoryUpdated": status.ok,
                            "activationAllowed": status.ok,
                            "activationOk": activation_ok,
                            "activationMode": "exact_balance_proof_rpc",
                        }),
                    );
                    return;
                }
                Ok(_) => {}
                Err(err) => {
                    emit_selected_candidate_sniper_event(
                        &cfg,
                        json!({
                            "kind": "rustSelectedCandidateExactBalanceProofRpcError",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "elapsedMs": started.elapsed().as_millis(),
                            "error": err.to_string(),
                        }),
                    );
                }
            }
        }

        if Instant::now() >= deadline {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateExactBalanceProofMissing",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "quotedTokensOutRaw": quoted_tokens_out_raw,
                    "timeoutMs": timeout_ms,
                    "elapsedMs": started.elapsed().as_millis(),
                    "balanceCacheEnabled": cfg.balance_cache_enabled,
                    "rpcFallbackEnabled": cfg.balance_cache_rpc_fallback_enabled,
                }),
            );
            return;
        }
        sleep(Duration::from_millis(cfg.exact_balance_proof_poll_ms)).await;
    }
}

fn selected_candidate_wallet_pubkey() -> Result<Pubkey> {
    let private_key = env::var("PRIVATE_KEY_BASE58").context("PRIVATE_KEY_BASE58 is not set")?;
    let keypair_bytes = bs58::decode(private_key.trim())
        .into_vec()
        .context("PRIVATE_KEY_BASE58 is not valid base58")?;
    let keypair = Keypair::from_bytes(&keypair_bytes)
        .context("PRIVATE_KEY_BASE58 is not a Solana keypair")?;
    Ok(keypair.pubkey())
}

fn selected_candidate_balance_grpc_endpoints(cfg: &Config) -> Vec<String> {
    let mut endpoints = env::var("RUST_SELECTED_CANDIDATE_BALANCE_GRPC_URLS")
        .ok()
        .map(|raw| {
            raw.split(',')
                .map(str::trim)
                .filter(|endpoint| !endpoint.is_empty())
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if endpoints.is_empty() {
        if let Some(endpoint) = env::var("RUST_SELECTED_CANDIDATE_BALANCE_GRPC_URL")
            .ok()
            .or_else(|| cfg.endpoint.clone())
        {
            if !endpoint.trim().is_empty() {
                endpoints.push(endpoint.trim().to_string());
            }
        }
    }
    endpoints.sort();
    endpoints.dedup();
    endpoints
}

fn selected_candidate_curve_grpc_endpoints(cfg: &Config) -> Vec<String> {
    let mut endpoints = env::var("RUST_SELECTED_CANDIDATE_CURVE_GRPC_URLS")
        .ok()
        .map(|raw| {
            raw.split(',')
                .map(str::trim)
                .filter(|endpoint| !endpoint.is_empty())
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if endpoints.is_empty() {
        endpoints.extend(selected_candidate_balance_grpc_endpoints(cfg));
    }
    if endpoints.is_empty() {
        if let Some(endpoint) = cfg.endpoint.clone() {
            if !endpoint.trim().is_empty() {
                endpoints.push(endpoint.trim().to_string());
            }
        }
    }
    endpoints.sort();
    endpoints.dedup();
    endpoints
}

fn derive_associated_token_address_main(
    owner: &Pubkey,
    mint: &Pubkey,
    token_program_id: &Pubkey,
) -> Result<Pubkey> {
    let associated_token_program_id =
        Pubkey::from_str("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
            .context("associated token program id invalid")?;
    Ok(Pubkey::find_program_address(
        &[owner.as_ref(), token_program_id.as_ref(), mint.as_ref()],
        &associated_token_program_id,
    )
    .0)
}

async fn run_selected_candidate_sniper(
    cfg: SelectedCandidateSniperConfig,
    hot: HotSniperConfig,
    curve_cache: CandidateCurveCache,
    shred_curve_replay: SelectedCandidateShredCurveReplay,
    exit_watcher: Option<exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let bind = format!("{}:{}", cfg.bind_host, cfg.bind_port);
    let listener = TcpListener::bind(&bind)
        .await
        .with_context(|| format!("selected candidate sniper bind failed at {bind}"))?;
    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateSniperStarted",
            "atMs": now_ms(),
            "bind": bind,
            "submitEnabled": cfg.submit_enabled,
            "requiredLane": cfg.required_lane,
            "requiredPolicy": cfg.required_policy,
            "maxEventLagMs": cfg.max_event_lag_ms,
            "maxSessionAttempts": cfg.max_session_buys,
            "maxOpenPositions": cfg.max_open_positions,
            "buyLamports": cfg.buy_lamports,
            "minEntryMcUsd": cfg.min_entry_mc_usd,
            "maxEntryMcUsd": cfg.max_entry_mc_usd,
            "maxEntryDriftBps": cfg.max_entry_drift_bps,
            "selectedEventFallbackMaxEntryDriftBps": cfg.selected_event_fallback_max_entry_drift_bps,
            "landingBufferBps": cfg.landing_buffer_bps,
            "selectedEventFallbackLandingBufferBps": cfg.selected_event_fallback_landing_buffer_bps,
            "feeQuoteHaircutBps": cfg.fee_quote_haircut_bps,
            "pumpfunTotalFeeBps": parse_env_u64(
                "RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS",
                PUMP_FUN_TRADE_FEE_BPS as u64,
            ).min(2_000),
            "maxMinOutBelowProtectedBps": cfg.max_min_out_below_protected_bps,
            "buyOutputPolicy": cfg.buy_output_policy.clone(),
            "fillFirst": selected_candidate_fill_first_buy_policy(&cfg),
            "maxEntryMarketCapUsd": cfg.max_entry_market_cap_usd,
            "postBuyRejectFillBelowProtectedMin": cfg.post_buy_reject_fill_below_protected_min,
            "reserveObservationSource": cfg.reserve_observation_source,
            "reserveObservationStartAtEnd": cfg.reserve_observation_start_at_end,
            "reserveObservationPollMs": cfg.reserve_observation_poll_ms,
            "minSendTimeMcDriftPct": cfg.min_send_time_mc_drift_pct,
            "maxSendTimeMcDriftPct": cfg.max_send_time_mc_drift_pct,
            "assumedShadowExitRoiPct": cfg.assumed_shadow_exit_roi_pct,
            "minLiveEntryAdjustedRoiPct": cfg.min_live_entry_adjusted_roi_pct,
            "sendTimeCurveRefreshEnabled": parse_env_bool(
                "RUST_SELECTED_CANDIDATE_SEND_TIME_CURVE_REFRESH_ENABLED",
                false
            ),
            "sendTimeCurveCacheRequired": parse_env_bool(
                "RUST_SELECTED_CANDIDATE_SEND_TIME_CURVE_CACHE_REQUIRED",
                false
            ),
            "sendTimeSelectedEventFallbackEnabled": parse_env_bool(
                "RUST_SELECTED_CANDIDATE_SEND_TIME_SELECTED_EVENT_FALLBACK_ENABLED",
                false
            ),
            "curveCacheEnabled": cfg.curve_cache_enabled,
            "curveCacheOwnerWide": cfg.curve_cache_owner_wide,
            "curveCacheRequireIndependent": cfg.curve_cache_require_independent,
            "curveCacheWaitMs": cfg.curve_cache_wait_ms,
            "curveCacheMaxAgeMs": cfg.curve_cache_max_age_ms,
            "curveRpcFallbackEnabled": cfg.curve_rpc_fallback_enabled,
            "optimisticExitActivateEnabled": cfg.optimistic_exit_activate_enabled,
            "earlyBalanceActivateEnabled": cfg.early_balance_activate_enabled,
            "earlyBalanceTimeoutMs": cfg.early_balance_timeout_ms,
            "earlyBalanceRpcFallbackEnabled": cfg.early_balance_rpc_fallback_enabled,
            "exactBalanceProofEnabled": cfg.exact_balance_proof_enabled,
            "exactBalanceProofTimeoutMs": cfg.exact_balance_proof_timeout_ms,
            "exactBalanceProofPollMs": cfg.exact_balance_proof_poll_ms,
            "exactBalanceProofRpcTimeoutMs": cfg.exact_balance_proof_rpc_timeout_ms,
            "directAtaBalanceProbeEnabled": cfg.direct_ata_balance_probe_enabled,
            "directAtaBalanceProbePerCandidateEnabled": cfg.direct_ata_balance_probe_per_candidate_enabled,
            "allowPreProofSellBroadcast": cfg.allow_pre_proof_sell_broadcast,
            "preProofSellRequireLocalLanded": cfg.pre_proof_sell_require_local_landed,
            "signatureOnlyBalanceProofEnabled": selected_candidate_signature_only_balance_proof_enabled(),
            "landedSafeExitEnabled": cfg.landed_safe_exit_enabled,
            "landedSafeExitTimeoutMs": cfg.landed_safe_exit_timeout_ms,
            "landedSafeExitPollMs": cfg.landed_safe_exit_poll_ms,
            "landedSafeExitHistoryFallbackEnabled": cfg.landed_safe_exit_history_fallback_enabled,
            "landedSafeExitRpcFallbackEnabled": cfg.landed_safe_exit_rpc_fallback_enabled,
            "guaranteedMinOutExitEnabled": cfg.guaranteed_min_out_exit_enabled,
            "guaranteedMinOutExitBps": cfg.guaranteed_min_out_exit_bps,
            "balanceCachePrebuyWatchSettleMs": cfg.balance_cache_prebuy_watch_settle_ms,
            "balanceCacheWalletOwnerStreamEnabled": cfg.balance_cache_wallet_owner_stream_enabled,
            "balanceCacheWaitMs": cfg.balance_cache_wait_ms,
            "balanceCacheMaxAgeMs": cfg.balance_cache_max_age_ms,
            "balanceCacheRpcFallbackEnabled": cfg.balance_cache_rpc_fallback_enabled,
        }),
    );
    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateLaneAllowList",
            "atMs": now_ms(),
            "requiredLane": cfg.required_lane,
            "allowedLanes": cfg.allowed_lanes.clone(),
            "requiredPolicy": cfg.required_policy,
        }),
    );
    loop {
        let (stream, peer) = listener.accept().await?;
        let client_cfg = cfg.clone();
        let client_hot = hot.clone();
        let client_curve_cache = curve_cache.clone();
        let client_shred_curve_replay = shred_curve_replay.clone();
        let client_exit_watcher = exit_watcher.clone();
        let client_exit_actions = exit_actions.clone();
        tokio::spawn(async move {
            emit_selected_candidate_sniper_event(
                &client_cfg,
                json!({
                    "kind": "rustSelectedCandidateTcpAccepted",
                    "atMs": now_ms(),
                    "peer": peer.to_string(),
                }),
            );
            if let Err(err) = handle_selected_candidate_stream(
                stream,
                client_cfg.clone(),
                client_hot,
                client_curve_cache,
                client_shred_curve_replay,
                client_exit_watcher,
                client_exit_actions,
            )
            .await
            {
                emit_selected_candidate_sniper_event(
                    &client_cfg,
                    json!({
                        "kind": "rustSelectedCandidateTcpError",
                        "atMs": now_ms(),
                        "peer": peer.to_string(),
                        "error": err.to_string(),
                    }),
                );
            }
        });
    }
}

async fn handle_selected_candidate_stream(
    stream: TcpStream,
    cfg: SelectedCandidateSniperConfig,
    hot: HotSniperConfig,
    curve_cache: CandidateCurveCache,
    shred_curve_replay: SelectedCandidateShredCurveReplay,
    exit_watcher: Option<exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let mut lines = BufReader::new(stream).lines();
    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        handle_selected_candidate_line(
            &cfg,
            &hot,
            &curve_cache,
            &shred_curve_replay,
            exit_watcher.as_ref(),
            exit_actions.as_ref(),
            &line,
        )
        .await;
    }
    Ok(())
}

async fn run_selected_candidate_file_selector(
    cfg: SelectedCandidateSniperConfig,
    hot: HotSniperConfig,
    curve_cache: CandidateCurveCache,
    shred_curve_replay: SelectedCandidateShredCurveReplay,
    exit_watcher: Option<exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<UnboundedSender<exit_watcher::ExitAction>>,
) {
    let Some(source) = cfg.candidate_source.clone() else {
        return;
    };
    if cfg.candidate_start_at_end {
        if let Ok(meta) = fs::metadata(&source) {
            cfg.candidate_offset
                .store(meta.len().min(u64::MAX), Ordering::SeqCst);
        }
    }
    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateFileSelectorStarted",
            "atMs": now_ms(),
            "source": source,
            "startAtEnd": cfg.candidate_start_at_end,
            "pollMs": cfg.candidate_poll_ms,
        }),
    );
    let mut carry = String::new();
    loop {
        if let Err(err) = process_selected_candidate_file_once(
            &cfg,
            &hot,
            &curve_cache,
            &shred_curve_replay,
            exit_watcher.as_ref(),
            exit_actions.as_ref(),
            &source,
            &mut carry,
        )
        .await
        {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateFileSelectorError",
                    "atMs": now_ms(),
                    "source": source,
                    "error": err.to_string(),
                }),
            );
        }
        sleep(Duration::from_millis(cfg.candidate_poll_ms)).await;
    }
}

async fn process_selected_candidate_file_once(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    shred_curve_replay: &SelectedCandidateShredCurveReplay,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
    source: &str,
    carry: &mut String,
) -> Result<()> {
    if !Path::new(source).exists() {
        return Ok(());
    }
    let data =
        fs::read(source).with_context(|| format!("read selected candidate source {source}"))?;
    let position = cfg.candidate_offset.load(Ordering::SeqCst) as usize;
    if data.len() < position {
        cfg.candidate_offset.store(0, Ordering::SeqCst);
        carry.clear();
        return Ok(());
    }
    if data.len() == position {
        return Ok(());
    }
    let chunk = String::from_utf8_lossy(&data[position..]).to_string();
    cfg.candidate_offset
        .store(data.len().min(u64::MAX as usize) as u64, Ordering::SeqCst);
    carry.push_str(&chunk);
    let mut lines = carry.split('\n').map(str::to_string).collect::<Vec<_>>();
    *carry = lines.pop().unwrap_or_default();
    for line in lines {
        if line.trim().is_empty() {
            continue;
        }
        handle_selected_candidate_line(
            cfg,
            hot,
            curve_cache,
            shred_curve_replay,
            exit_watcher,
            exit_actions,
            &line,
        )
        .await;
    }
    Ok(())
}

async fn handle_raw_candidate_line(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    shred_curve_replay: &SelectedCandidateShredCurveReplay,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
    line: &str,
) {
    let row = match serde_json::from_str::<Value>(line) {
        Ok(row) => row,
        Err(err) => {
            selected_candidate_reject(
                cfg,
                "raw_candidate_invalid_json",
                json!({ "error": err.to_string() }),
            );
            return;
        }
    };
    match selected_candidate_from_raw_candidate(cfg, hot, curve_cache, &row).await {
        Ok(Some(candidate)) => {
            let selected_event = raw_candidate_selected_event(cfg, &candidate);
            emit_selected_candidate_sniper_event(cfg, selected_event);
            handle_selected_candidate_object(
                cfg,
                hot,
                curve_cache,
                shred_curve_replay,
                exit_watcher,
                exit_actions,
                candidate,
            )
            .await;
        }
        Ok(None) => {}
        Err(err) => {
            selected_candidate_reject(
                cfg,
                "raw_candidate_selector_error",
                json!({ "error": err.to_string() }),
            );
        }
    }
}

async fn handle_selected_candidate_object(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    shred_curve_replay: &SelectedCandidateShredCurveReplay,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
    candidate: SelectedCandidate,
) {
    let started = Instant::now();
    submit_selected_candidate_buy(
        cfg,
        hot,
        curve_cache,
        shred_curve_replay,
        exit_watcher,
        exit_actions,
        candidate,
        started,
    )
    .await;
}

async fn handle_selected_candidate_line(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    shred_curve_replay: &SelectedCandidateShredCurveReplay,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
    line: &str,
) {
    let row = match serde_json::from_str::<Value>(line) {
        Ok(row) => row,
        Err(err) => {
            selected_candidate_reject(cfg, "invalid_json", json!({ "error": err.to_string() }));
            return;
        }
    };
    if json_string_at(&row, &["schemaVersion"]).as_deref()
        == Some("two-cq-lead-attribution-candidate-1")
    {
        match selected_candidate_from_raw_candidate(cfg, hot, curve_cache, &row).await {
            Ok(Some(candidate)) => {
                let selected_event = raw_candidate_selected_event(cfg, &candidate);
                emit_selected_candidate_sniper_event(cfg, selected_event);
                handle_selected_candidate_object(
                    cfg,
                    hot,
                    curve_cache,
                    shred_curve_replay,
                    exit_watcher,
                    exit_actions,
                    candidate,
                )
                .await;
            }
            Ok(None) => {}
            Err(err) => {
                selected_candidate_reject(
                    cfg,
                    "raw_candidate_tcp_selector_error",
                    json!({ "error": err.to_string() }),
                );
            }
        }
        return;
    }
    if json_string_at(&row, &["schemaVersion"]).as_deref()
        != Some("two-cq-continuous-shadow-event-1")
        || json_string_at(&row, &["event"]).as_deref() != Some("candidate_selected")
    {
        return;
    }
    let Some(candidate) = selected_candidate_from_row(cfg, curve_cache, &row) else {
        return;
    };
    handle_selected_candidate_object(
        cfg,
        hot,
        curve_cache,
        shred_curve_replay,
        exit_watcher,
        exit_actions,
        candidate,
    )
    .await;
}

async fn submit_selected_candidate_buy(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    shred_curve_replay: &SelectedCandidateShredCurveReplay,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
    mut candidate: SelectedCandidate,
    started: Instant,
) {
    let candidate_selected_at_ms = now_ms().min(u64::MAX as u128) as u64;
    if let Some(reason) =
        prepare_selected_candidate_fast_exit_template(cfg, curve_cache, &mut candidate)
    {
        selected_candidate_reject(
            cfg,
            &reason,
            json!({
                "key": candidate.key,
                "mint": candidate.mint,
                "bondingCurve": candidate.bonding_curve,
                "creator": candidate.creator,
                "tokenProgramId": candidate.token_program_id,
            }),
        );
        return;
    }
    if let Some(reason) = reserve_selected_candidate(cfg, hot, &candidate) {
        selected_candidate_reject(
            cfg,
            &reason,
            json!({ "key": candidate.key, "mint": candidate.mint }),
        );
        return;
    }
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateAccepted",
            "atMs": now_ms(),
            "candidateSelectedAtMs": candidate_selected_at_ms,
            "key": candidate.key,
            "mint": candidate.mint,
            "sourceSignature": candidate.source_signature,
            "entryMarketCapUsd": candidate.entry_market_cap_usd,
            "eventLagMs": candidate.event_lag_ms,
            "reserveSource": candidate.reserve_source,
            "reserveSlot": candidate.reserve_slot,
            "reserveObservedAtMs": candidate.reserve_observed_at_ms,
            "nativeFeatures": candidate.native_features.clone(),
            "submitEnabled": cfg.submit_enabled,
            "noSubmitDryBuildEnabled": cfg.no_submit_dry_build_enabled,
        }),
    );
    if !cfg.submit_enabled {
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateDryAccepted",
                "atMs": now_ms(),
                "key": candidate.key,
                "mint": candidate.mint,
                "elapsedMs": started.elapsed().as_millis(),
                "willDryBuild": cfg.no_submit_dry_build_enabled,
            }),
        );
        if !cfg.no_submit_dry_build_enabled {
            release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
            return;
        }
    }

    if cfg.submit_enabled && cfg.early_balance_activate_enabled && cfg.balance_cache_enabled {
        match cfg.balance_cache.watch_candidate(&candidate) {
            Ok(ata) => {
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateBalanceCacheWatch",
                        "atMs": now_ms(),
                        "mint": candidate.mint,
                        "ata": ata,
                        "tokenProgramId": candidate.token_program_id,
                        "directAtaProbeEnabled": cfg.direct_ata_balance_probe_enabled,
                        "directAtaProbePerCandidateEnabled": cfg.direct_ata_balance_probe_per_candidate_enabled,
                    }),
                );
                if cfg.direct_ata_balance_probe_enabled
                    && cfg.direct_ata_balance_probe_per_candidate_enabled
                    && cfg.exact_balance_proof_enabled
                    && parse_env_bool(
                        "RUST_SELECTED_CANDIDATE_DIRECT_ATA_BALANCE_PROBE_PREBUY_ENABLED",
                        false,
                    )
                {
                    spawn_selected_candidate_direct_ata_balance_probe(
                        cfg, &candidate, ata, "pre_buy",
                    );
                }
            }
            Err(err) => emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateBalanceCacheWatchError",
                    "atMs": now_ms(),
                    "mint": candidate.mint,
                    "error": err.to_string(),
                }),
            ),
        }
    }

    if cfg.submit_enabled
        && cfg.early_balance_activate_enabled
        && cfg.balance_cache_enabled
        && cfg.exact_balance_proof_enabled
        && cfg.balance_cache_prebuy_watch_settle_ms > 0
    {
        let cfg_for_settle = cfg.clone();
        let key_for_settle = candidate.key.clone();
        let mint_for_settle = candidate.mint.clone();
        let settle_ms = cfg.balance_cache_prebuy_watch_settle_ms;
        tokio::spawn(async move {
            let settle_started = Instant::now();
            sleep(Duration::from_millis(settle_ms)).await;
            emit_selected_candidate_sniper_event(
                &cfg_for_settle,
                json!({
                    "kind": "rustSelectedCandidateBalanceCachePrebuyWatchSettled",
                    "atMs": now_ms(),
                    "key": key_for_settle,
                    "mint": mint_for_settle,
                    "settleMs": settle_ms,
                    "elapsedMs": settle_started.elapsed().as_millis(),
                    "nonBlocking": true,
                    "criticalPathBlocked": false,
                }),
            );
        });
    }

    let mut immediate_yellowstone_check_at_ms: Option<u64> = None;
    let mut authoritative_state_ready_at_ms: Option<u64> = None;
    let mut final_guard_at_ms: Option<u64> = None;
    let mut final_guard_complete_at_ms: Option<u64> = None;
    let mut final_guard_entry_mc_usd: Option<f64> = None;
    let mut final_guard_expected_tokens_raw: Option<u64> = None;
    let mut final_guard_reserve_observed_at_ms: Option<u64> = None;
    let mut pre_sign_guard_at_ms: Option<u64> = None;
    let mut pre_sign_guard_entry_mc_usd: Option<f64> = None;
    let mut pre_sign_guard_expected_tokens_raw: Option<u64> = None;
    let mut pre_sign_guard_reserve_observed_at_ms: Option<u64> = None;
    let mut pre_sign_guard_reserve_slot: Option<u64> = None;
    let mut pre_sign_guard_reserve_source: Option<&'static str> = None;

    if parse_env_bool(
        "RUST_SELECTED_CANDIDATE_SEND_TIME_CURVE_REFRESH_ENABLED",
        false,
    ) {
        if cfg.curve_cache_enabled {
            curve_cache.watch(&candidate.bonding_curve);
        }
        immediate_yellowstone_check_at_ms = Some(now_ms().min(u64::MAX as u128) as u64);
        match selected_candidate_curve_snapshot(
            cfg,
            hot,
            curve_cache,
            &candidate.key,
            &candidate.mint,
            &candidate.bonding_curve,
        )
        .await
        {
            Ok(snapshot) => {
                authoritative_state_ready_at_ms = Some(now_ms().min(u64::MAX as u128) as u64);
                let baseline_mc_sol = estimate_market_cap_sol(&CurveState {
                    virtual_token_reserves: candidate.virtual_token_reserves,
                    virtual_sol_reserves: candidate.virtual_sol_reserves,
                    token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
                    complete: false,
                    creator: None,
                });
                let current_mc_usd = baseline_mc_sol
                    .filter(|value| value.is_finite() && *value > 0.0)
                    .and_then(|baseline| {
                        estimate_market_cap_sol(&snapshot.state)
                            .map(|current| current * (candidate.entry_market_cap_usd / baseline))
                    });
                let send_time_mc_drift_pct = current_mc_usd.and_then(|current_mc| {
                    if candidate.entry_market_cap_usd.is_finite()
                        && candidate.entry_market_cap_usd > 0.0
                        && current_mc.is_finite()
                    {
                        Some(((current_mc / candidate.entry_market_cap_usd) - 1.0) * 100.0)
                    } else {
                        None
                    }
                });
                let live_entry_adjusted_roi_pct = cfg
                    .assumed_shadow_exit_roi_pct
                    .zip(send_time_mc_drift_pct)
                    .map(|(shadow_roi_pct, drift_pct)| {
                        (((1.0 + (shadow_roi_pct / 100.0)) / (1.0 + (drift_pct / 100.0))) - 1.0)
                            * 100.0
                    })
                    .filter(|value| value.is_finite());
                if let (Some(adjusted_roi_pct), Some(min_adjusted_roi_pct)) = (
                    live_entry_adjusted_roi_pct,
                    cfg.min_live_entry_adjusted_roi_pct,
                ) {
                    if adjusted_roi_pct < min_adjusted_roi_pct {
                        release_selected_candidate_reservations(
                            cfg,
                            hot,
                            &candidate,
                            cfg.buy_lamports,
                        );
                        selected_candidate_reject(
                            cfg,
                            "live_entry_adjusted_roi_below_min",
                            json!({
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                                "sendTimeEntryMarketCapUsd": current_mc_usd,
                                "sendTimeMcDriftPct": send_time_mc_drift_pct,
                                "assumedShadowExitRoiPct": cfg.assumed_shadow_exit_roi_pct,
                                "liveEntryAdjustedRoiPct": adjusted_roi_pct,
                                "minLiveEntryAdjustedRoiPct": min_adjusted_roi_pct,
                                "reserveSource": snapshot.source,
                                "reserveSlot": snapshot.slot,
                                "reserveObservedAtMs": snapshot.observed_at_ms,
                            }),
                        );
                        return;
                    }
                }
                if let (Some(drift_pct), Some(min_drift_pct)) =
                    (send_time_mc_drift_pct, cfg.min_send_time_mc_drift_pct)
                {
                    if drift_pct < min_drift_pct {
                        release_selected_candidate_reservations(
                            cfg,
                            hot,
                            &candidate,
                            cfg.buy_lamports,
                        );
                        selected_candidate_reject(
                            cfg,
                            "send_time_mc_drift_below_min",
                            json!({
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                                "sendTimeEntryMarketCapUsd": current_mc_usd,
                                "sendTimeMcDriftPct": drift_pct,
                                "minSendTimeMcDriftPct": min_drift_pct,
                                "reserveSource": snapshot.source,
                                "reserveSlot": snapshot.slot,
                                "reserveObservedAtMs": snapshot.observed_at_ms,
                            }),
                        );
                        return;
                    }
                }
                if let (Some(drift_pct), Some(max_drift_pct)) =
                    (send_time_mc_drift_pct, cfg.max_send_time_mc_drift_pct)
                {
                    if drift_pct > max_drift_pct {
                        release_selected_candidate_reservations(
                            cfg,
                            hot,
                            &candidate,
                            cfg.buy_lamports,
                        );
                        selected_candidate_reject(
                            cfg,
                            "send_time_mc_drift_above_max",
                            json!({
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                                "sendTimeEntryMarketCapUsd": current_mc_usd,
                                "sendTimeMcDriftPct": drift_pct,
                                "maxSendTimeMcDriftPct": max_drift_pct,
                                "reserveSource": snapshot.source,
                                "reserveSlot": snapshot.slot,
                                "reserveObservedAtMs": snapshot.observed_at_ms,
                            }),
                        );
                        return;
                    }
                }
                if let (Some(current_mc), Some(max_mc)) =
                    (current_mc_usd, cfg.max_entry_market_cap_usd)
                {
                    if current_mc > max_mc {
                        release_selected_candidate_reservations(
                            cfg,
                            hot,
                            &candidate,
                            cfg.buy_lamports,
                        );
                        selected_candidate_reject(
                            cfg,
                            "send_time_entry_mc_above_max",
                            json!({
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                                "sendTimeEntryMarketCapUsd": current_mc,
                                "maxEntryMarketCapUsd": max_mc,
                                "reserveSource": snapshot.source,
                                "reserveSlot": snapshot.slot,
                                "reserveObservedAtMs": snapshot.observed_at_ms,
                            }),
                        );
                        return;
                    }
                }
                let send_time_quote_tokens_raw = quote_buy_tokens_out_raw_main(
                    snapshot.state.virtual_sol_reserves as u128,
                    snapshot.state.virtual_token_reserves as u128,
                    cfg.buy_lamports as u128,
                )
                .ok()
                .and_then(|value| u64::try_from(value).ok());
                let send_time_calibrated_quote_tokens_raw = send_time_quote_tokens_raw
                    .map(|quote| apply_bps_haircut_u64(quote, cfg.fee_quote_haircut_bps));
                let effective_max_entry_drift_bps = if snapshot.source == "selected-event-fallback"
                {
                    cfg.selected_event_fallback_max_entry_drift_bps
                } else {
                    cfg.max_entry_drift_bps
                };
                let mut allowed_drift_bps = effective_max_entry_drift_bps;
                if let Some(max_mc) = cfg
                    .max_entry_market_cap_usd
                    .filter(|value| value.is_finite() && *value > 0.0)
                {
                    let cap_bps = ((max_mc / candidate.entry_market_cap_usd) - 1.0) * 10_000.0;
                    if cap_bps.is_finite() && cap_bps >= 0.0 {
                        allowed_drift_bps = allowed_drift_bps.min(cap_bps.floor() as u64);
                    }
                }
                let expected_shadow_tokens = ((candidate.shadow_tokens_out_raw as u128)
                    * (cfg.buy_lamports as u128))
                    / (candidate.shadow_sol_in_lamports.max(1) as u128);
                let protected_min_tokens_out_raw = (expected_shadow_tokens * 10_000u128)
                    / (10_000u128 + allowed_drift_bps as u128);
                let protected_floor_tokens_out_raw = apply_bps_haircut_u128(
                    protected_min_tokens_out_raw,
                    cfg.max_min_out_below_protected_bps,
                );
                if let Some(calibrated_quote) = send_time_calibrated_quote_tokens_raw {
                    let protected_floor_u64 =
                        u64::try_from(protected_floor_tokens_out_raw.max(1)).unwrap_or(u64::MAX);
                    if calibrated_quote < protected_floor_u64 {
                        release_selected_candidate_reservations(
                            cfg,
                            hot,
                            &candidate,
                            cfg.buy_lamports,
                        );
                        selected_candidate_reject(
                            cfg,
                            "send_time_calibrated_quote_below_protected_floor",
                            json!({
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                                "sendTimeEntryMarketCapUsd": current_mc_usd,
                                "sendTimeQuoteTokensRaw": send_time_quote_tokens_raw,
                                "sendTimeCalibratedQuoteTokensRaw": send_time_calibrated_quote_tokens_raw,
                                "protectedMinTokensOutRaw": u64::try_from(protected_min_tokens_out_raw).ok(),
                                "protectedFloorTokensOutRaw": protected_floor_u64,
                                "feeQuoteHaircutBps": cfg.fee_quote_haircut_bps,
                                "maxMinOutBelowProtectedBps": cfg.max_min_out_below_protected_bps,
                                "reserveSource": snapshot.source,
                                "reserveSlot": snapshot.slot,
                                "reserveObservedAtMs": snapshot.observed_at_ms,
                            }),
                        );
                        return;
                    }
                }
                let buffered_min_tokens_out_raw = send_time_calibrated_quote_tokens_raw
                    .map(|quote| apply_bps_haircut_u64(quote, cfg.landing_buffer_bps));
                let allow_below_protected = parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_ALLOW_LANDING_BUFFER_BELOW_PROTECTED_MIN",
                    false,
                );
                let fill_first = selected_candidate_fill_first_buy_policy(cfg);
                let submitted_min_tokens_out_raw = if fill_first {
                    Some(1)
                } else if allow_below_protected {
                    buffered_min_tokens_out_raw.map(|buffered| {
                        buffered.max(
                            u64::try_from(protected_floor_tokens_out_raw.max(1))
                                .unwrap_or(u64::MAX),
                        )
                    })
                } else {
                    u64::try_from(protected_min_tokens_out_raw.max(1)).ok()
                };
                let guard_at_ms = now_ms().min(u64::MAX as u128) as u64;
                final_guard_at_ms = Some(guard_at_ms);
                final_guard_complete_at_ms = Some(guard_at_ms);
                final_guard_entry_mc_usd = current_mc_usd;
                final_guard_expected_tokens_raw = send_time_calibrated_quote_tokens_raw;
                final_guard_reserve_observed_at_ms = Some(snapshot.observed_at_ms);
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateSendTimeCurveRefresh",
                        "atMs": guard_at_ms,
                        "candidateSelectedAtMs": candidate_selected_at_ms,
                        "immediateYellowstoneCheckAtMs": immediate_yellowstone_check_at_ms,
                        "authoritativeStateReadyAtMs": authoritative_state_ready_at_ms,
                        "finalGuardCompleteAtMs": final_guard_complete_at_ms,
                        "selectedToImmediateCurveCheckMs": immediate_yellowstone_check_at_ms.map(|check_at| {
                            check_at.saturating_sub(candidate_selected_at_ms)
                        }),
                        "selectedToAuthoritativeReadyMs": authoritative_state_ready_at_ms.map(|ready_at| {
                            ready_at.saturating_sub(candidate_selected_at_ms)
                        }),
                        "finalEconomicGuardAtMs": guard_at_ms,
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                        "sendTimeEntryMarketCapUsd": current_mc_usd,
                        "sendTimeMcDriftPct": send_time_mc_drift_pct,
                        "minSendTimeMcDriftPct": cfg.min_send_time_mc_drift_pct,
                        "maxSendTimeMcDriftPct": cfg.max_send_time_mc_drift_pct,
                        "assumedShadowExitRoiPct": cfg.assumed_shadow_exit_roi_pct,
                        "liveEntryAdjustedRoiPct": live_entry_adjusted_roi_pct,
                        "minLiveEntryAdjustedRoiPct": cfg.min_live_entry_adjusted_roi_pct,
                        "sendTimeQuoteTokensRaw": send_time_quote_tokens_raw,
                        "sendTimeCalibratedQuoteTokensRaw": send_time_calibrated_quote_tokens_raw,
                        "protectedMinTokensOutRaw": u64::try_from(protected_min_tokens_out_raw).ok(),
                        "protectedFloorTokensOutRaw": u64::try_from(protected_floor_tokens_out_raw).ok(),
                        "bufferedMinTokensOutRaw": buffered_min_tokens_out_raw,
                        "submittedMinTokensOutRaw": submitted_min_tokens_out_raw,
                        "buyOutputPolicy": cfg.buy_output_policy.clone(),
                        "fillFirst": fill_first,
                        "fillFirstMinOutRaw": if fill_first { Some(1_u64) } else { None },
                        "economicEntryProtection": "send_time_mc_drift_and_protected_quote_floor",
                        "allowedDriftBps": allowed_drift_bps,
                        "effectiveMaxEntryDriftBps": effective_max_entry_drift_bps,
                        "landingBufferBps": cfg.landing_buffer_bps,
                        "feeQuoteHaircutBps": cfg.fee_quote_haircut_bps,
                        "maxMinOutBelowProtectedBps": cfg.max_min_out_below_protected_bps,
                        "pumpfunTotalFeeBps": parse_env_u64(
                            "RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS",
                            PUMP_FUN_TRADE_FEE_BPS as u64,
                        )
                        .min(2_000),
                        "reserveSource": snapshot.source,
                        "reserveSlot": snapshot.slot,
                        "reserveObservedAtMs": snapshot.observed_at_ms,
                        "reserveAgeMs": guard_at_ms.saturating_sub(snapshot.observed_at_ms),
                    }),
                );
                if parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_SHREDSTREAM_CANONICAL_REPLAY_SEND_TIME_SEED_ENABLED",
                    true,
                ) {
                    if let Some(seed_slot) = snapshot.slot {
                        match shred_curve_replay.seed_from_selected_candidate_snapshot(
                            &candidate.bonding_curve,
                            snapshot.state.clone(),
                            seed_slot,
                            snapshot.observed_at_ms,
                            "selected-candidate-send-time-seed",
                        ) {
                            Ok(seeded) => emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateShredstreamCanonicalReplaySeeded",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "bondingCurve": candidate.bonding_curve,
                                    "seeded": seeded,
                                    "seedSource": "selected-candidate-send-time-seed",
                                    "snapshotSource": snapshot.source,
                                    "slot": seed_slot,
                                    "observedAtMs": snapshot.observed_at_ms,
                                    "ageMs": (now_ms().min(u64::MAX as u128) as u64).saturating_sub(snapshot.observed_at_ms),
                                }),
                            ),
                            Err(reason) => emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateShredstreamCanonicalReplaySeedError",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "bondingCurve": candidate.bonding_curve,
                                    "seedSource": "selected-candidate-send-time-seed",
                                    "snapshotSource": snapshot.source,
                                    "slot": seed_slot,
                                    "observedAtMs": snapshot.observed_at_ms,
                                    "reason": reason,
                                }),
                            ),
                        }
                    } else {
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidateShredstreamCanonicalReplaySeedSkipped",
                                "atMs": now_ms(),
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "bondingCurve": candidate.bonding_curve,
                                "reason": "send_time_snapshot_missing_slot",
                                "snapshotSource": snapshot.source,
                                "observedAtMs": snapshot.observed_at_ms,
                            }),
                        );
                    }
                }
                candidate.virtual_sol_reserves = snapshot.state.virtual_sol_reserves;
                candidate.virtual_token_reserves = snapshot.state.virtual_token_reserves;
                candidate.reserve_slot = snapshot.slot;
                candidate.reserve_observed_at_ms = Some(snapshot.observed_at_ms);
                candidate.reserve_source = snapshot.source;
            }
            Err(err) => {
                if cfg.submit_enabled
                    && selected_candidate_fill_first_buy_policy(cfg)
                    && !parse_env_bool(
                        "RUST_SELECTED_CANDIDATE_FILL_FIRST_ALLOW_SELECTED_EVENT_FALLBACK",
                        false,
                    )
                {
                    release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                    selected_candidate_reject(
                        cfg,
                        "fill_first_send_time_curve_cache_miss",
                        json!({
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "bondingCurve": candidate.bonding_curve,
                            "error": err.to_string(),
                            "buyOutputPolicy": cfg.buy_output_policy,
                            "fillFirst": true,
                            "submitEnabled": cfg.submit_enabled,
                            "selectedEventFallbackBlocked": true,
                            "curveCacheWaitMs": cfg.curve_cache_wait_ms,
                            "curveCacheMaxAgeMs": cfg.curve_cache_max_age_ms,
                        }),
                    );
                    return;
                }
                if parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_SEND_TIME_CURVE_CACHE_REQUIRED",
                    false,
                ) {
                    release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                    selected_candidate_reject(
                        cfg,
                        "send_time_curve_cache_required_miss",
                        json!({
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "bondingCurve": candidate.bonding_curve,
                            "error": err.to_string(),
                            "cacheRequired": true,
                            "selectedEventFallbackEnabled": parse_env_bool(
                                "RUST_SELECTED_CANDIDATE_SEND_TIME_SELECTED_EVENT_FALLBACK_ENABLED",
                                false
                            ),
                            "curveCacheWaitMs": cfg.curve_cache_wait_ms,
                            "curveCacheMaxAgeMs": cfg.curve_cache_max_age_ms,
                        }),
                    );
                    return;
                }
                if !parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_SEND_TIME_SELECTED_EVENT_FALLBACK_ENABLED",
                    false,
                ) {
                    release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                    selected_candidate_reject(
                        cfg,
                        "send_time_curve_refresh_failed",
                        json!({
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "bondingCurve": candidate.bonding_curve,
                            "error": err.to_string(),
                            "cacheRequired": parse_env_bool(
                                "RUST_SELECTED_CANDIDATE_SEND_TIME_CURVE_CACHE_REQUIRED",
                                false
                            ),
                            "selectedEventFallbackEnabled": false,
                        }),
                    );
                    return;
                }
                let snapshot = selected_candidate_selected_event_curve_snapshot(&candidate);
                let snapshot_age_ms =
                    (now_ms().min(u64::MAX as u128) as u64).saturating_sub(snapshot.observed_at_ms);
                let max_snapshot_age_ms = parse_env_u64(
                    "RUST_SELECTED_CANDIDATE_SELECTED_EVENT_FALLBACK_MAX_AGE_MS",
                    cfg.curve_cache_max_age_ms.max(cfg.max_event_lag_ms),
                )
                .clamp(10, 5_000);
                if snapshot_age_ms > max_snapshot_age_ms {
                    release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                    selected_candidate_reject(
                        cfg,
                        "send_time_selected_event_snapshot_stale",
                        json!({
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "bondingCurve": candidate.bonding_curve,
                            "error": err.to_string(),
                            "snapshotAgeMs": snapshot_age_ms,
                            "maxSnapshotAgeMs": max_snapshot_age_ms,
                        }),
                    );
                    return;
                }
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateSendTimeCurveFallback",
                        "atMs": now_ms(),
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "bondingCurve": candidate.bonding_curve,
                        "fallbackSource": snapshot.source,
                        "fallbackSlot": snapshot.slot,
                        "fallbackObservedAtMs": snapshot.observed_at_ms,
                        "fallbackAgeMs": snapshot_age_ms,
                        "fallbackMaxEntryDriftBps": cfg.selected_event_fallback_max_entry_drift_bps,
                        "fallbackLandingBufferBps": cfg.selected_event_fallback_landing_buffer_bps,
                        "cacheError": err.to_string(),
                    }),
                );
                candidate.reserve_slot = snapshot.slot;
                candidate.reserve_observed_at_ms = Some(snapshot.observed_at_ms);
                candidate.reserve_source = snapshot.source;
            }
        }
    }

    let blockhash = match hot_blockhash(hot).await {
        Ok(blockhash) => blockhash,
        Err(err) => {
            release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
            selected_candidate_reject(
                cfg,
                "blockhash_fetch_failed",
                json!({ "key": candidate.key, "mint": candidate.mint, "error": err.to_string() }),
            );
            return;
        }
    };
    if cfg.submit_enabled
        && selected_candidate_fill_first_buy_policy(cfg)
        && parse_env_bool(
            "RUST_SELECTED_CANDIDATE_PRE_SIGN_ECONOMIC_GUARD_ENABLED",
            true,
        )
    {
        let pre_sign_at_ms = now_ms().min(u64::MAX as u128) as u64;
        let Some(cached) =
            curve_cache.get_fresh_yellowstone(&candidate.bonding_curve, cfg.curve_cache_max_age_ms)
        else {
            release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
            selected_candidate_reject(
                cfg,
                "pre_sign_yellowstone_curve_cache_miss",
                json!({
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "bondingCurve": candidate.bonding_curve,
                    "buyOutputPolicy": cfg.buy_output_policy,
                    "fillFirst": true,
                    "curveCacheMaxAgeMs": cfg.curve_cache_max_age_ms,
                    "finalEconomicGuardAtMs": final_guard_at_ms,
                    "finalEconomicGuardExpectedTokensRaw": final_guard_expected_tokens_raw,
                }),
            );
            return;
        };
        let baseline_mc_sol = estimate_market_cap_sol(&CurveState {
            virtual_token_reserves: candidate.virtual_token_reserves,
            virtual_sol_reserves: candidate.virtual_sol_reserves,
            token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
            complete: false,
            creator: None,
        });
        let current_mc_usd = baseline_mc_sol
            .filter(|value| value.is_finite() && *value > 0.0)
            .and_then(|baseline| {
                estimate_market_cap_sol(&cached.state)
                    .map(|current| current * (candidate.entry_market_cap_usd / baseline))
            });
        let pre_sign_mc_drift_pct = current_mc_usd.and_then(|current_mc| {
            if candidate.entry_market_cap_usd.is_finite()
                && candidate.entry_market_cap_usd > 0.0
                && current_mc.is_finite()
            {
                Some(((current_mc / candidate.entry_market_cap_usd) - 1.0) * 100.0)
            } else {
                None
            }
        });
        if let (Some(current_mc), Some(max_mc)) = (current_mc_usd, cfg.max_entry_market_cap_usd) {
            if current_mc > max_mc {
                release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                selected_candidate_reject(
                    cfg,
                    "pre_sign_entry_mc_above_max",
                    json!({
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                        "preSignEntryMarketCapUsd": current_mc,
                        "maxEntryMarketCapUsd": max_mc,
                        "preSignReserveSource": cached.source,
                        "preSignReserveSlot": cached.slot,
                        "preSignReserveObservedAtMs": cached.observed_at_ms,
                        "preSignReserveAgeMs": pre_sign_at_ms.saturating_sub(cached.observed_at_ms),
                    }),
                );
                return;
            }
        }
        if let (Some(drift_pct), Some(max_drift_pct)) =
            (pre_sign_mc_drift_pct, cfg.max_send_time_mc_drift_pct)
        {
            if drift_pct > max_drift_pct {
                release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                selected_candidate_reject(
                    cfg,
                    "pre_sign_mc_drift_above_max",
                    json!({
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                        "preSignEntryMarketCapUsd": current_mc_usd,
                        "preSignMcDriftPct": drift_pct,
                        "maxSendTimeMcDriftPct": max_drift_pct,
                        "preSignReserveSource": cached.source,
                        "preSignReserveSlot": cached.slot,
                        "preSignReserveObservedAtMs": cached.observed_at_ms,
                    }),
                );
                return;
            }
        }
        let pre_sign_quote_tokens_raw = quote_buy_tokens_out_raw_main(
            cached.state.virtual_sol_reserves as u128,
            cached.state.virtual_token_reserves as u128,
            cfg.buy_lamports as u128,
        )
        .ok()
        .and_then(|value| u64::try_from(value).ok());
        let pre_sign_calibrated_quote_tokens_raw = pre_sign_quote_tokens_raw
            .map(|quote| apply_bps_haircut_u64(quote, cfg.fee_quote_haircut_bps));
        let effective_max_entry_drift_bps = if candidate.reserve_source == "selected-event-fallback"
        {
            cfg.selected_event_fallback_max_entry_drift_bps
        } else {
            cfg.max_entry_drift_bps
        };
        let mut allowed_drift_bps = effective_max_entry_drift_bps;
        if let Some(max_mc) = cfg
            .max_entry_market_cap_usd
            .filter(|value| value.is_finite() && *value > 0.0)
        {
            let cap_bps = ((max_mc / candidate.entry_market_cap_usd) - 1.0) * 10_000.0;
            if cap_bps.is_finite() && cap_bps >= 0.0 {
                allowed_drift_bps = allowed_drift_bps.min(cap_bps.floor() as u64);
            }
        }
        let expected_shadow_tokens = ((candidate.shadow_tokens_out_raw as u128)
            * (cfg.buy_lamports as u128))
            / (candidate.shadow_sol_in_lamports.max(1) as u128);
        let protected_min_tokens_out_raw =
            (expected_shadow_tokens * 10_000u128) / (10_000u128 + allowed_drift_bps as u128);
        let protected_floor_tokens_out_raw = apply_bps_haircut_u128(
            protected_min_tokens_out_raw,
            cfg.max_min_out_below_protected_bps,
        );
        if let Some(calibrated_quote) = pre_sign_calibrated_quote_tokens_raw {
            let protected_floor_u64 =
                u64::try_from(protected_floor_tokens_out_raw.max(1)).unwrap_or(u64::MAX);
            if calibrated_quote < protected_floor_u64 {
                release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                selected_candidate_reject(
                    cfg,
                    "pre_sign_calibrated_quote_below_protected_floor",
                    json!({
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                        "preSignEntryMarketCapUsd": current_mc_usd,
                        "preSignQuoteTokensRaw": pre_sign_quote_tokens_raw,
                        "preSignCalibratedQuoteTokensRaw": pre_sign_calibrated_quote_tokens_raw,
                        "protectedMinTokensOutRaw": u64::try_from(protected_min_tokens_out_raw).ok(),
                        "protectedFloorTokensOutRaw": protected_floor_u64,
                        "feeQuoteHaircutBps": cfg.fee_quote_haircut_bps,
                        "maxMinOutBelowProtectedBps": cfg.max_min_out_below_protected_bps,
                        "preSignReserveSource": cached.source,
                        "preSignReserveSlot": cached.slot,
                        "preSignReserveObservedAtMs": cached.observed_at_ms,
                    }),
                );
                return;
            }
        }
        if let (Some(final_quote), Some(pre_sign_quote)) = (
            final_guard_expected_tokens_raw,
            pre_sign_calibrated_quote_tokens_raw,
        ) {
            let max_drop_bps = parse_env_u64(
                "RUST_SELECTED_CANDIDATE_PRE_SIGN_MAX_EXPECTED_OUTPUT_DROP_BPS",
                2_500,
            )
            .min(10_000);
            if let Some(drop_bps) =
                selected_candidate_expected_output_drop_bps(final_quote, pre_sign_quote)
            {
                if drop_bps > max_drop_bps {
                    release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                    selected_candidate_reject(
                        cfg,
                        "pre_sign_expected_output_deteriorated",
                        json!({
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                            "preSignEntryMarketCapUsd": current_mc_usd,
                            "finalEconomicGuardExpectedTokensRaw": final_quote,
                            "preSignCalibratedQuoteTokensRaw": pre_sign_quote,
                            "expectedOutputDropBps": drop_bps,
                            "maxExpectedOutputDropBps": max_drop_bps,
                            "preSignReserveSource": cached.source,
                            "preSignReserveSlot": cached.slot,
                            "preSignReserveObservedAtMs": cached.observed_at_ms,
                        }),
                    );
                    return;
                }
            }
        }
        pre_sign_guard_at_ms = Some(pre_sign_at_ms);
        pre_sign_guard_entry_mc_usd = current_mc_usd;
        pre_sign_guard_expected_tokens_raw = pre_sign_calibrated_quote_tokens_raw;
        pre_sign_guard_reserve_observed_at_ms = Some(cached.observed_at_ms);
        pre_sign_guard_reserve_slot = Some(cached.slot);
        pre_sign_guard_reserve_source = Some(cached.source);
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidatePreSignEconomicGuard",
                "atMs": pre_sign_at_ms,
                "key": candidate.key,
                "mint": candidate.mint,
                "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                "preSignEntryMarketCapUsd": current_mc_usd,
                "preSignMcDriftPct": pre_sign_mc_drift_pct,
                "preSignQuoteTokensRaw": pre_sign_quote_tokens_raw,
                "preSignCalibratedQuoteTokensRaw": pre_sign_calibrated_quote_tokens_raw,
                "preSignReserveSource": cached.source,
                "preSignReserveSlot": cached.slot,
                "preSignReserveObservedAtMs": cached.observed_at_ms,
                "preSignReserveAgeMs": pre_sign_at_ms.saturating_sub(cached.observed_at_ms),
                "finalEconomicGuardAtMs": final_guard_at_ms,
                "finalEconomicGuardExpectedTokensRaw": final_guard_expected_tokens_raw,
                "preSignMaxExpectedOutputDropBps": parse_env_u64(
                    "RUST_SELECTED_CANDIDATE_PRE_SIGN_MAX_EXPECTED_OUTPUT_DROP_BPS",
                    2_500,
                )
                .min(10_000),
            }),
        );
        candidate.virtual_sol_reserves = cached.state.virtual_sol_reserves;
        candidate.virtual_token_reserves = cached.state.virtual_token_reserves;
        candidate.reserve_slot = Some(cached.slot);
        candidate.reserve_observed_at_ms = Some(cached.observed_at_ms);
        candidate.reserve_source = cached.source;
    }
    let (priority_fee_micro_lamports, jito_tip_lamports, helius_tip_lamports) =
        selected_candidate_buy_fees(hot, cfg.buy_lamports);
    let effective_landing_buffer_bps = if candidate.reserve_source == "selected-event-fallback" {
        cfg.selected_event_fallback_landing_buffer_bps
    } else {
        cfg.landing_buffer_bps
    };
    let effective_max_entry_drift_bps = if candidate.reserve_source == "selected-event-fallback" {
        cfg.selected_event_fallback_max_entry_drift_bps
    } else {
        cfg.max_entry_drift_bps
    };
    let min_out_plan = selected_candidate_min_out_plan(
        cfg,
        &candidate,
        effective_max_entry_drift_bps,
        effective_landing_buffer_bps,
    );
    let command = executor::ExecutePumpfunBuyCommand {
        id: format!(
            "rust-selected-{}-{}",
            now_ms(),
            candidate.key.chars().take(8).collect::<String>()
        ),
        mode: "direct".to_string(),
        route: "pumpfun-rust-selected-candidate-buy".to_string(),
        mint: candidate.mint.clone(),
        source_signature: Some(candidate.source_signature.clone()),
        quote_mint: candidate.quote_mint.clone(),
        sol_in_lamports: cfg.buy_lamports,
        tokens_out_raw: None,
        min_tokens_out_raw: None,
        slippage_bps: hot.slippage_bps,
        buy_output_policy: Some(cfg.buy_output_policy.clone()),
        exact_sol_in: hot.exact_sol_in,
        source_ix_accounts: candidate.source_ix_accounts.clone(),
        source_ix_data_base64: candidate.source_ix_data_base64.clone(),
        bonding_curve: Some(candidate.bonding_curve.clone()),
        creator: Some(candidate.creator.clone()),
        token_program_id: candidate.token_program_id.clone(),
        gate_virtual_sol_reserves: Some(candidate.virtual_sol_reserves),
        gate_virtual_token_reserves: Some(candidate.virtual_token_reserves),
        gate_reserve_slot: candidate.reserve_slot,
        gate_reserve_observed_at_ms: candidate.reserve_observed_at_ms,
        native_entry_drift_gate: true,
        shadow_entry_market_cap_usd: Some(candidate.entry_market_cap_usd),
        shadow_tokens_out_raw: Some(candidate.shadow_tokens_out_raw),
        shadow_sol_in_lamports: Some(candidate.shadow_sol_in_lamports),
        max_entry_drift_bps: Some(effective_max_entry_drift_bps),
        landing_buffer_bps: Some(effective_landing_buffer_bps),
        max_entry_market_cap_usd: cfg.max_entry_market_cap_usd,
        blockhash,
        compute_unit_limit: hot.compute_unit_limit,
        priority_fee_micro_lamports,
        jito_tip_lamports,
        helius_tip_lamports,
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
    let guaranteed_submitted_min_tokens_out_raw =
        match executor::guaranteed_min_tokens_out_raw_for_pumpfun_buy_command(&command) {
            Ok(value) => Some(value.max(1)),
            Err(err) => {
                if cfg.guaranteed_min_out_exit_enabled {
                    release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                    selected_candidate_reject(
                        cfg,
                        "guaranteed_min_out_unavailable",
                        json!({
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "error": err.to_string(),
                            "reason": "guaranteed_min_out_exit_enabled_requires_exact_submitted_buy_amount",
                        }),
                    );
                    return;
                }
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateGuaranteedMinOutUnavailable",
                        "atMs": now_ms(),
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "error": err.to_string(),
                    }),
                );
                None
            }
        };
    let source_ix_data_info =
        selected_candidate_source_ix_data_info(candidate.source_ix_data_base64.as_deref());
    let source_ix_account_count = candidate
        .source_ix_accounts
        .as_ref()
        .map(|accounts| accounts.len());
    let submitted_buy_max_sol_cost_lamports = if hot.exact_sol_in {
        cfg.buy_lamports
    } else {
        cfg.buy_lamports
            .saturating_mul(10_000u64.saturating_add(hot.slippage_bps))
            / 10_000
    };
    let executor_buy_instruction_audit = executor::pumpfun_buy_command_data_audit(&command)
        .map_err(|err| err.to_string())
        .map_or_else(
            |error| json!({ "ok": false, "error": error }),
            |audit| json!({ "ok": true, "audit": audit }),
        );
    let buy_start_at_ms = now_ms().min(u64::MAX as u128) as u64;
    let final_guard_to_buy_start_max_ms = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_FINAL_GUARD_TO_BUY_START_MAX_MS",
        150,
    );
    if cfg.submit_enabled && final_guard_to_buy_start_max_ms > 0 {
        if let Some(guard_at) = final_guard_at_ms {
            let elapsed = buy_start_at_ms.saturating_sub(guard_at);
            if elapsed > final_guard_to_buy_start_max_ms {
                release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                selected_candidate_reject(
                    cfg,
                    "final_guard_to_buy_start_deadline_exceeded",
                    json!({
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "finalEconomicGuardAtMs": final_guard_at_ms,
                        "buyStartAtMs": buy_start_at_ms,
                        "finalEconomicGuardToBuyStartMs": elapsed,
                        "maxFinalEconomicGuardToBuyStartMs": final_guard_to_buy_start_max_ms,
                        "preSignEconomicGuardAtMs": pre_sign_guard_at_ms,
                        "preSignReserveSource": pre_sign_guard_reserve_source,
                        "preSignReserveSlot": pre_sign_guard_reserve_slot,
                    }),
                );
                return;
            }
        }
    }
    emit_selected_candidate_sniper_event(
        cfg,
            json!({
                "kind": "rustSelectedCandidateBuyStart",
                "atMs": buy_start_at_ms,
                "candidateSelectedAtMs": candidate_selected_at_ms,
                "immediateYellowstoneCheckAtMs": immediate_yellowstone_check_at_ms,
                "authoritativeStateReadyAtMs": authoritative_state_ready_at_ms,
                "finalGuardCompleteAtMs": final_guard_complete_at_ms,
                "selectedToImmediateCurveCheckMs": immediate_yellowstone_check_at_ms.map(|check_at| {
                    check_at.saturating_sub(candidate_selected_at_ms)
                }),
                "selectedToAuthoritativeReadyMs": authoritative_state_ready_at_ms.map(|ready_at| {
                    ready_at.saturating_sub(candidate_selected_at_ms)
                }),
                "selectedToBuyStartMs": buy_start_at_ms.saturating_sub(candidate_selected_at_ms),
                "authoritativeReadyToBuyStartMs": authoritative_state_ready_at_ms.map(|ready_at| {
                    buy_start_at_ms.saturating_sub(ready_at)
                }),
                "key": candidate.key,
            "mint": candidate.mint,
            "sourceSignature": candidate.source_signature,
            "entryMarketCapUsd": candidate.entry_market_cap_usd,
            "finalEconomicGuardAtMs": final_guard_at_ms,
            "finalEconomicGuardToBuildMs": final_guard_at_ms
                .map(|guard_at| buy_start_at_ms.saturating_sub(guard_at)),
            "finalEconomicGuardEntryMarketCapUsd": final_guard_entry_mc_usd,
            "finalEconomicGuardExpectedTokensRaw": final_guard_expected_tokens_raw,
            "finalEconomicGuardReserveObservedAtMs": final_guard_reserve_observed_at_ms,
            "preSignEconomicGuardAtMs": pre_sign_guard_at_ms,
            "preSignEconomicGuardToBuyStartMs": pre_sign_guard_at_ms
                .map(|guard_at| buy_start_at_ms.saturating_sub(guard_at)),
            "preSignEconomicGuardEntryMarketCapUsd": pre_sign_guard_entry_mc_usd,
            "preSignEconomicGuardExpectedTokensRaw": pre_sign_guard_expected_tokens_raw,
            "preSignEconomicGuardReserveObservedAtMs": pre_sign_guard_reserve_observed_at_ms,
            "preSignReserveSource": pre_sign_guard_reserve_source,
            "preSignReserveSlot": pre_sign_guard_reserve_slot,
            "reserveSource": candidate.reserve_source,
            "reserveSlot": candidate.reserve_slot,
            "reserveObservedAtMs": candidate.reserve_observed_at_ms,
            "buyLamports": cfg.buy_lamports,
            "priorityFeeMicroLamports": priority_fee_micro_lamports,
            "jitoTipLamports": jito_tip_lamports,
            "heliusTipLamports": helius_tip_lamports,
            "landingBufferBps": cfg.landing_buffer_bps,
            "selectedEventFallbackLandingBufferBps": cfg.selected_event_fallback_landing_buffer_bps,
            "effectiveLandingBufferBps": effective_landing_buffer_bps,
            "feeQuoteHaircutBps": cfg.fee_quote_haircut_bps,
            "pumpfunTotalFeeBps": parse_env_u64(
                "RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS",
                PUMP_FUN_TRADE_FEE_BPS as u64,
            )
            .min(2_000),
            "maxMinOutBelowProtectedBps": cfg.max_min_out_below_protected_bps,
            "buyOutputPolicy": cfg.buy_output_policy.clone(),
            "fillFirst": selected_candidate_fill_first_buy_policy(cfg),
            "fillFirstMinOutRaw": if selected_candidate_fill_first_buy_policy(cfg) { Some(1_u64) } else { None },
            "maxEntryDriftBps": cfg.max_entry_drift_bps,
            "selectedEventFallbackMaxEntryDriftBps": cfg.selected_event_fallback_max_entry_drift_bps,
            "effectiveMaxEntryDriftBps": effective_max_entry_drift_bps,
            "minOutPlan": min_out_plan.clone(),
            "buyInstructionAudit": {
                "mode": "direct",
                "exactSolIn": hot.exact_sol_in,
                "tokenProgramId": candidate.token_program_id,
                "sourceIxAccountCount": source_ix_account_count,
                "sourceIxDataLen": source_ix_data_info.len,
                "sourceIxDataDiscriminatorHex": source_ix_data_info.discriminator_hex,
                "sourceIxDataDiscriminatorKind": source_ix_data_info.discriminator_kind,
                "submittedBuyAmountArgRaw": guaranteed_submitted_min_tokens_out_raw,
                "submittedBuyMaxSolCostLamports": submitted_buy_max_sol_cost_lamports,
                "slippageBps": hot.slippage_bps,
            },
            "executorBuyInstructionAudit": executor_buy_instruction_audit.clone(),
            "guaranteedSubmittedMinTokensOutRaw": guaranteed_submitted_min_tokens_out_raw,
            "guaranteedMinOutExitEnabled": cfg.guaranteed_min_out_exit_enabled,
            "guaranteedMinOutExitBps": cfg.guaranteed_min_out_exit_bps,
            "submitEnabled": cfg.submit_enabled,
            "noSubmitDryBuildEnabled": cfg.no_submit_dry_build_enabled,
        }),
    );
    if !cfg.submit_enabled && cfg.no_submit_dry_build_enabled {
        match executor::dry_build_pumpfun_buy_command(command, started) {
            Ok(response) if response.ok => {
                release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateNoSubmitDryBuildReady",
                        "atMs": now_ms(),
                        "ok": true,
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "sourceSignature": candidate.source_signature,
                        "entryMarketCapUsd": candidate.entry_market_cap_usd,
                        "finalEconomicGuardAtMs": final_guard_at_ms,
                        "finalEconomicGuardEntryMarketCapUsd": final_guard_entry_mc_usd,
                        "finalEconomicGuardExpectedTokensRaw": final_guard_expected_tokens_raw,
                        "finalEconomicGuardReserveObservedAtMs": final_guard_reserve_observed_at_ms,
                        "preSignEconomicGuardAtMs": pre_sign_guard_at_ms,
                        "preSignEconomicGuardEntryMarketCapUsd": pre_sign_guard_entry_mc_usd,
                        "preSignEconomicGuardExpectedTokensRaw": pre_sign_guard_expected_tokens_raw,
                        "preSignEconomicGuardReserveObservedAtMs": pre_sign_guard_reserve_observed_at_ms,
                        "preSignReserveSource": pre_sign_guard_reserve_source,
                        "preSignReserveSlot": pre_sign_guard_reserve_slot,
                        "reserveSource": candidate.reserve_source,
                        "reserveSlot": candidate.reserve_slot,
                        "reserveObservedAtMs": candidate.reserve_observed_at_ms,
                        "reserveAgeMs": candidate.reserve_observed_at_ms.map(|observed| {
                            (now_ms().min(u64::MAX as u128) as u64).saturating_sub(observed)
                        }),
                        "buyLamports": cfg.buy_lamports,
                        "route": response.route,
                        "senderPath": response.sender_path,
                        "signature": response.signature,
                        "tokensOutRaw": response.tokens_out_raw,
                        "solInLamports": response.sol_in_lamports,
                        "elapsedMs": response.elapsed_ms,
                        "buildMs": response.build_ms,
                        "signMs": response.sign_ms,
                        "serializeMs": response.serialize_ms,
                        "broadcastMs": response.broadcast_ms,
                        "broadcastFirstAckMs": response.broadcast_first_ack_ms,
                        "broadcastCandidateCount": response.broadcast_candidate_count,
                        "priorityGraceMs": response.priority_grace_ms,
                        "priorityGraceDetached": response.priority_grace_detached,
                        "txBytes": response.tx_bytes,
                        "selectedToNoSubmitDryBuildReadyMs": started.elapsed().as_millis(),
                        "minOutPlan": min_out_plan,
                    }),
                );
            }
            Ok(response) => {
                release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                selected_candidate_reject(
                    cfg,
                    "no_submit_dry_build_failed",
                    json!({
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "error": response.error,
                        "elapsedMs": response.elapsed_ms,
                    }),
                );
            }
            Err(err) => {
                release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
                selected_candidate_reject(
                    cfg,
                    "no_submit_dry_build_error",
                    json!({
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "error": err.to_string(),
                        "elapsedMs": started.elapsed().as_millis(),
                    }),
                );
            }
        }
        return;
    }
    match executor::execute_pumpfun_buy_command(command, started).await {
        Ok(response) if response.ok => {
            let signature = response.signature.unwrap_or_default();
            let tokens_out_raw = response.tokens_out_raw.unwrap_or(1).max(1);
            let sol_in_lamports = response.sol_in_lamports.unwrap_or(cfg.buy_lamports).max(1);
            let buy_result_at_ms = now_ms();
            let first_send_at_ms = response.first_send_elapsed_ms.map(|elapsed| {
                u128::from(candidate_selected_at_ms).saturating_add(elapsed)
            });
            let first_ack_at_ms = response.first_ack_elapsed_ms.map(|elapsed| {
                u128::from(candidate_selected_at_ms).saturating_add(elapsed)
            });
            let fill_first_buy = selected_candidate_fill_first_buy_policy(cfg);
            let pre_exact_min_out_exit_enabled =
                cfg.guaranteed_min_out_exit_enabled && !fill_first_buy;
            let guaranteed_min_out_safe_sell_tokens_raw = if pre_exact_min_out_exit_enabled {
                guaranteed_submitted_min_tokens_out_raw.map(|submitted_min| {
                    let pre_exact_cap_bps = parse_env_u64(
                        "RUST_SELECTED_CANDIDATE_PRE_EXACT_SAFE_SELL_BPS_OF_SUBMITTED_MIN",
                        cfg.guaranteed_min_out_exit_bps,
                    )
                    .clamp(1, 10_000);
                    selected_candidate_pre_exact_safe_sell_tokens_raw(
                        submitted_min,
                        tokens_out_raw,
                        cfg.guaranteed_min_out_exit_bps,
                        pre_exact_cap_bps,
                    )
                })
            } else {
                None
            };
            let pre_exact_safe_sell_bps_of_submitted_min = if pre_exact_min_out_exit_enabled {
                Some(
                    parse_env_u64(
                        "RUST_SELECTED_CANDIDATE_PRE_EXACT_SAFE_SELL_BPS_OF_SUBMITTED_MIN",
                        cfg.guaranteed_min_out_exit_bps,
                    )
                    .clamp(1, 10_000),
                )
            } else {
                None
            };
            let safe_sell_tokens_raw = if fill_first_buy {
                None
            } else if pre_exact_min_out_exit_enabled {
                guaranteed_min_out_safe_sell_tokens_raw
            } else if cfg.optimistic_exit_activate_enabled {
                let protected_safe = selected_candidate_protected_min_tokens_out_raw(
                    cfg,
                    &candidate,
                    effective_max_entry_drift_bps,
                );
                let unproven_bps = parse_env_u64(
                    "RUST_SELECTED_CANDIDATE_OPTIMISTIC_UNPROVEN_SELL_BPS_OF_SUBMITTED_MIN",
                    10_000,
                )
                .min(10_000);
                let unproven_safe = (unproven_bps < 10_000).then(|| {
                    ((tokens_out_raw as u128 * unproven_bps as u128) / 10_000_u128)
                        .max(1)
                        .min(tokens_out_raw as u128) as u64
                });
                protected_safe
                    .or(unproven_safe)
                    .map(|value| {
                        unproven_safe
                            .map(|unproven| value.min(unproven))
                            .unwrap_or(value)
                            .min(tokens_out_raw)
                            .max(1)
                    })
                    .filter(|value| *value < tokens_out_raw)
            } else {
                None
            };
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateBuyResult",
                    "atMs": buy_result_at_ms,
                    "candidateSelectedAtMs": candidate_selected_at_ms,
                    "immediateYellowstoneCheckAtMs": immediate_yellowstone_check_at_ms,
                    "authoritativeStateReadyAtMs": authoritative_state_ready_at_ms,
                    "finalGuardCompleteAtMs": final_guard_complete_at_ms,
                    "firstSendAtMs": first_send_at_ms,
                    "firstAckAtMs": first_ack_at_ms,
                    "buyResultAtMs": buy_result_at_ms,
                    "selectedToImmediateCurveCheckMs": immediate_yellowstone_check_at_ms.map(|check_at| {
                        check_at.saturating_sub(candidate_selected_at_ms)
                    }),
                    "selectedToAuthoritativeReadyMs": authoritative_state_ready_at_ms.map(|ready_at| {
                        ready_at.saturating_sub(candidate_selected_at_ms)
                    }),
                    "selectedToFirstSendMs": response.first_send_elapsed_ms,
                    "authoritativeReadyToFirstSendMs": authoritative_state_ready_at_ms
                        .zip(first_send_at_ms)
                        .map(|(ready_at, send_at)| send_at.saturating_sub(u128::from(ready_at))),
                    "ok": true,
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "signature": signature,
                    "solInLamports": sol_in_lamports,
                    "tokensOutRaw": tokens_out_raw,
                    "economicTokensHeldRaw": tokens_out_raw,
                    "finalEconomicGuardAtMs": final_guard_at_ms,
                    "finalEconomicGuardToBuyResultMs": final_guard_at_ms.map(|guard_at| {
                        buy_result_at_ms.saturating_sub(guard_at as u128)
                    }),
                    "finalEconomicGuardEntryMarketCapUsd": final_guard_entry_mc_usd,
                    "finalEconomicGuardExpectedTokensRaw": final_guard_expected_tokens_raw,
                    "finalEconomicGuardReserveObservedAtMs": final_guard_reserve_observed_at_ms,
                    "preSignEconomicGuardAtMs": pre_sign_guard_at_ms,
                    "preSignEconomicGuardToBuyResultMs": pre_sign_guard_at_ms.map(|guard_at| {
                        buy_result_at_ms.saturating_sub(guard_at as u128)
                    }),
                    "preSignEconomicGuardEntryMarketCapUsd": pre_sign_guard_entry_mc_usd,
                    "preSignEconomicGuardExpectedTokensRaw": pre_sign_guard_expected_tokens_raw,
                    "preSignEconomicGuardReserveObservedAtMs": pre_sign_guard_reserve_observed_at_ms,
                    "preSignReserveSource": pre_sign_guard_reserve_source,
                    "preSignReserveSlot": pre_sign_guard_reserve_slot,
                    "safeSellTokensRaw": safe_sell_tokens_raw,
                    "priorityFeeMicroLamports": priority_fee_micro_lamports,
                    "jitoTipLamports": jito_tip_lamports,
                    "heliusTipLamports": helius_tip_lamports,
                    "guaranteedSubmittedMinTokensOutRaw": guaranteed_submitted_min_tokens_out_raw,
                    "guaranteedMinOutSafeSellTokensRaw": guaranteed_min_out_safe_sell_tokens_raw,
                    "guaranteedMinOutExitEnabled": cfg.guaranteed_min_out_exit_enabled,
                    "preExactMinOutExitEnabled": pre_exact_min_out_exit_enabled,
                    "guaranteedMinOutExitBps": cfg.guaranteed_min_out_exit_bps,
                    "preExactSafeSellBpsOfSubmittedMin": pre_exact_safe_sell_bps_of_submitted_min,
                    "buyOutputPolicy": cfg.buy_output_policy.clone(),
                    "fillFirst": fill_first_buy,
                    "submittedBuyAmountArgRaw": guaranteed_submitted_min_tokens_out_raw,
                    "submittedBuyMaxSolCostLamports": submitted_buy_max_sol_cost_lamports,
                    "buyInstructionAudit": {
                        "mode": "direct",
                        "exactSolIn": hot.exact_sol_in,
                        "tokenProgramId": candidate.token_program_id,
                        "sourceIxAccountCount": source_ix_account_count,
                        "sourceIxDataLen": source_ix_data_info.len,
                        "sourceIxDataDiscriminatorHex": source_ix_data_info.discriminator_hex,
                        "sourceIxDataDiscriminatorKind": source_ix_data_info.discriminator_kind,
                        "slippageBps": hot.slippage_bps,
                    },
                    "executorBuyInstructionAudit": executor_buy_instruction_audit,
                    "route": response.route,
                    "senderPath": response.sender_path,
                    "elapsedMs": response.elapsed_ms,
                    "buildMs": response.build_ms,
                    "signMs": response.sign_ms,
                    "serializeMs": response.serialize_ms,
                    "firstSendElapsedMs": response.first_send_elapsed_ms,
                    "firstAckElapsedMs": response.first_ack_elapsed_ms,
                    "broadcastMs": response.broadcast_ms,
                    "broadcastFirstAckMs": response.broadcast_first_ack_ms,
                    "broadcastCandidateCount": response.broadcast_candidate_count,
                    "priorityGraceMs": response.priority_grace_ms,
                    "priorityGraceDetached": response.priority_grace_detached,
                    "txBytes": response.tx_bytes,
                    "selectedToBuyResultMs": started.elapsed().as_millis(),
                }),
            );
            if cfg.direct_ata_balance_probe_enabled
                && cfg.direct_ata_balance_probe_per_candidate_enabled
                && cfg.exact_balance_proof_enabled
                && !parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_DIRECT_ATA_BALANCE_PROBE_PREBUY_ENABLED",
                    false,
                )
            {
                match selected_candidate_ata_for_mint(
                    &candidate.mint,
                    candidate.token_program_id.as_deref(),
                ) {
                    Ok((ata, _)) => spawn_selected_candidate_direct_ata_balance_probe(
                        cfg,
                        &candidate,
                        ata,
                        "post_buy_result",
                    ),
                    Err(err) => emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateDirectAtaBalanceProbeSpawnError",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "timing": "post_buy_result",
                            "error": err.to_string(),
                        }),
                    ),
                }
            }
            if cfg.balance_cache_enabled && !signature.is_empty() {
                match cfg.balance_cache.remember_submitted_buy(
                    signature.clone(),
                    &candidate,
                    tokens_out_raw,
                ) {
                    Ok(preseen_landed) => {
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidateSubmittedBuyProofRegistered",
                                "atMs": now_ms(),
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "signature": signature,
                                "amountRaw": tokens_out_raw,
                                "exact": false,
                                "balanceSource": "submitted-buy-result",
                                "preseenShredstreamLanded": preseen_landed.is_some(),
                                "preseenShredstreamSlot": preseen_landed.as_ref().map(|cached| cached.slot),
                                "preseenShredstreamObservedAtMs": preseen_landed.as_ref().map(|cached| cached.observed_at_ms),
                            }),
                        );
                        if let Some(cached) = preseen_landed {
                            emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateShredstreamSubmittedBuyProofUpdate",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "signature": signature,
                                    "amountRaw": cached.amount_raw,
                                    "slot": cached.slot,
                                    "observedAtMs": cached.observed_at_ms,
                                    "exact": cached.exact,
                                    "balanceSource": cached.source,
                                    "proofTiming": "preseen_before_buy_registration",
                                }),
                            );
                        }
                    }
                    Err(err) => emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateSubmittedBuyProofRegisterError",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "signature": signature,
                            "error": err.to_string(),
                        }),
                    ),
                }
            }
            if let (Some(registry), Some(action_tx)) = (exit_watcher, exit_actions) {
                let exit_watch_registered = register_selected_candidate_exit_watch(
                    cfg,
                    hot,
                    curve_cache,
                    registry,
                    action_tx,
                    &candidate,
                    tokens_out_raw,
                    safe_sell_tokens_raw,
                    sol_in_lamports,
                    false,
                );
                let exit_watch_activated = Arc::new(AtomicBool::new(false));
                let early_balance_proved = Arc::new(AtomicBool::new(false));
                let optimistic_unproven_enabled = parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_OPTIMISTIC_EXIT_UNPROVEN_BALANCE_ENABLED",
                    false,
                );
                if pre_exact_min_out_exit_enabled
                    && cfg.allow_pre_proof_sell_broadcast
                    && !cfg.pre_proof_sell_require_local_landed
                    && exit_watch_registered
                    && safe_sell_tokens_raw.is_some()
                {
                    let activate_status = activate_selected_candidate_exit_watch(
                        cfg, registry, action_tx, &candidate,
                    );
                    if activate_status.ok {
                        exit_watch_activated.store(true, Ordering::SeqCst);
                    }
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateGuaranteedMinOutExitActivated",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "quotedTokensOutRaw": tokens_out_raw,
                            "economicTokensHeldRaw": tokens_out_raw,
                            "submittedMinTokensOutRaw": guaranteed_submitted_min_tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "preExactSafeSellBpsOfSubmittedMin": pre_exact_safe_sell_bps_of_submitted_min,
                            "inventoryUpdated": false,
                            "activationAllowed": true,
                            "activationOk": activate_status.ok,
                            "phase": activate_status.phase,
                            "error": activate_status.error,
                            "reason": "submitted_buy_min_out_guaranteed_safe_amount",
                            "experimentalPreProofBroadcast": false,
                            "guaranteeBasis": "buy_would_fail_unless_actual_tokens_gte_submitted_min_out",
                        }),
                    );
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateProvisionalFirstSellable",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "signature": signature,
                            "entryMarketCapUsd": candidate.entry_market_cap_usd,
                            "activationMode": "guaranteed_submitted_min_out",
                            "balanceSource": "submitted-buy-min-out-guarantee",
                            "quotedTokensOutRaw": tokens_out_raw,
                            "submittedMinTokensOutRaw": guaranteed_submitted_min_tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "rustAcceptedToFirstSellableMs": started.elapsed().as_millis(),
                            "buyResultToProvisionalFirstSellableMs": now_ms().saturating_sub(buy_result_at_ms),
                            "eventLagMs": candidate.event_lag_ms,
                        }),
                    );
                } else if pre_exact_min_out_exit_enabled
                    && exit_watch_registered
                    && safe_sell_tokens_raw.is_some()
                {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateGuaranteedMinOutExitArmed",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "quotedTokensOutRaw": tokens_out_raw,
                            "economicTokensHeldRaw": tokens_out_raw,
                            "submittedMinTokensOutRaw": guaranteed_submitted_min_tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "inventoryUpdated": false,
                            "activationAllowed": false,
                            "broadcastAllowed": false,
                            "reason": if cfg.allow_pre_proof_sell_broadcast && cfg.pre_proof_sell_require_local_landed {
                                "pre_proof_sell_requires_local_landed_proof"
                            } else {
                                "pre_proof_sell_broadcast_disabled"
                            },
                        }),
                    );
                } else if cfg.guaranteed_min_out_exit_enabled {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateGuaranteedMinOutExitSkipped",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "quotedTokensOutRaw": tokens_out_raw,
                            "submittedMinTokensOutRaw": guaranteed_submitted_min_tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "buyOutputPolicy": cfg.buy_output_policy.clone(),
                            "fillFirst": fill_first_buy,
                            "reason": if exit_watch_registered {
                                if fill_first_buy {
                                    "fill_first_min_out_is_not_inventory_proof"
                                } else {
                                    "safe_sell_amount_unavailable"
                                }
                            } else {
                                "exit_watch_not_registered"
                            },
                        }),
                    );
                } else if cfg.optimistic_exit_activate_enabled
                    && optimistic_unproven_enabled
                    && cfg.allow_pre_proof_sell_broadcast
                    && !cfg.pre_proof_sell_require_local_landed
                    && exit_watch_registered
                    && safe_sell_tokens_raw.is_some()
                {
                    let activate_status = activate_selected_candidate_exit_watch(
                        cfg, registry, action_tx, &candidate,
                    );
                    if activate_status.ok {
                        exit_watch_activated.store(true, Ordering::SeqCst);
                    }
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateOptimisticExitActivated",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "quotedTokensOutRaw": tokens_out_raw,
                            "economicTokensHeldRaw": tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "inventoryUpdated": false,
                            "activationAllowed": true,
                            "activationOk": activate_status.ok,
                            "phase": activate_status.phase,
                            "error": activate_status.error,
                            "reason": "unproven_balance_safe_amount",
                            "experimentalPreProofBroadcast": true,
                        }),
                    );
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateProvisionalFirstSellable",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "signature": signature,
                            "entryMarketCapUsd": candidate.entry_market_cap_usd,
                            "activationMode": "optimistic_unproven_safe_amount",
                            "balanceSource": "submitted-min-safe-provisional",
                            "quotedTokensOutRaw": tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "rustAcceptedToFirstSellableMs": started.elapsed().as_millis(),
                            "buyResultToProvisionalFirstSellableMs": now_ms().saturating_sub(buy_result_at_ms),
                            "eventLagMs": candidate.event_lag_ms,
                        }),
                    );
                } else if cfg.optimistic_exit_activate_enabled
                    && optimistic_unproven_enabled
                    && exit_watch_registered
                    && safe_sell_tokens_raw.is_some()
                {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateProvisionalExitArmed",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "quotedTokensOutRaw": tokens_out_raw,
                            "economicTokensHeldRaw": tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "inventoryUpdated": false,
                            "activationAllowed": false,
                            "broadcastAllowed": false,
                            "reason": if cfg.allow_pre_proof_sell_broadcast && cfg.pre_proof_sell_require_local_landed {
                                "pre_proof_sell_requires_local_landed_proof"
                            } else {
                                "pre_proof_sell_broadcast_disabled"
                            },
                            "note": "exit watcher is registered inert; exact balance proof must update inventory before TP/SL can sell",
                        }),
                    );
                } else if cfg.optimistic_exit_activate_enabled && exit_watch_registered {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateOptimisticExitSkipped",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "quotedTokensOutRaw": tokens_out_raw,
                            "economicTokensHeldRaw": tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "inventoryUpdated": false,
                            "activationAllowed": false,
                            "reason": if optimistic_unproven_enabled {
                                "safe_sell_amount_unavailable"
                            } else {
                                "optimistic_unproven_balance_disabled"
                            },
                            "note": "exit watcher is registered inert; exact balance proof must update inventory before TP/SL can sell",
                        }),
                    );
                } else if cfg.optimistic_exit_activate_enabled {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateOptimisticExitSkipped",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "reason": "exit_watch_not_registered",
                        }),
                    );
                }
                if cfg.landed_safe_exit_enabled
                    && exit_watch_registered
                    && (!cfg.allow_pre_proof_sell_broadcast
                        || cfg.pre_proof_sell_require_local_landed)
                    && safe_sell_tokens_raw.is_some()
                    && !signature.is_empty()
                {
                    let landed_cfg = cfg.clone();
                    let landed_hot = hot.clone();
                    let landed_registry = registry.clone();
                    let landed_action_tx = action_tx.clone();
                    let landed_candidate = candidate.clone();
                    let landed_signature = signature.clone();
                    let landed_activated = exit_watch_activated.clone();
                    let landed_exact_proved = early_balance_proved.clone();
                    let landed_safe_sell_tokens_raw = safe_sell_tokens_raw.unwrap_or(1);
                    tokio::spawn(async move {
                        run_selected_candidate_landed_safe_exit_activator(
                            landed_cfg,
                            landed_hot,
                            landed_registry,
                            landed_action_tx,
                            landed_candidate,
                            landed_signature,
                            tokens_out_raw,
                            landed_safe_sell_tokens_raw,
                            sol_in_lamports,
                            buy_result_at_ms,
                            started,
                            landed_activated,
                            landed_exact_proved,
                        )
                        .await;
                    });
                } else if cfg.landed_safe_exit_enabled {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateLandedSafeExitSkipped",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "signature": signature,
                            "exitWatchRegistered": exit_watch_registered,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "preProofSellBroadcastAllowed": cfg.allow_pre_proof_sell_broadcast,
                            "reason": if !exit_watch_registered {
                                "exit_watch_not_registered"
                            } else if safe_sell_tokens_raw.is_none() {
                                "safe_sell_amount_unavailable"
                            } else if signature.is_empty() {
                                "buy_signature_unavailable"
                            } else {
                                "pre_proof_sell_broadcast_already_enabled"
                            },
                        }),
                    );
                }
                if cfg.early_balance_activate_enabled && cfg.early_balance_timeout_ms > 0 {
                    let early_cfg = cfg.clone();
                    let early_hot = hot.clone();
                    let early_registry = registry.clone();
                    let early_action_tx = action_tx.clone();
                    let early_candidate = candidate.clone();
                    let early_activated = exit_watch_activated.clone();
                    let early_proved = early_balance_proved.clone();
                    tokio::spawn(async move {
                        run_selected_candidate_early_balance_activator(
                            early_cfg,
                            early_hot,
                            early_registry,
                            early_action_tx,
                            early_candidate,
                            tokens_out_raw,
                            tokens_out_raw,
                            sol_in_lamports,
                            early_activated,
                            early_proved,
                        )
                        .await;
                    });
                }
                if cfg.exact_balance_proof_enabled {
                    let proof_cfg = cfg.clone();
                    let proof_hot = hot.clone();
                    let proof_registry = registry.clone();
                    let proof_action_tx = action_tx.clone();
                    let proof_candidate = candidate.clone();
                    let proof_activated = exit_watch_activated.clone();
                    let proof_proved = early_balance_proved.clone();
                    tokio::spawn(async move {
                        run_selected_candidate_exact_balance_prover(
                            proof_cfg,
                            proof_hot,
                            proof_registry,
                            proof_action_tx,
                            proof_candidate,
                            tokens_out_raw,
                            sol_in_lamports,
                            proof_activated,
                            proof_proved,
                        )
                        .await;
                    });
                }
                let finality = confirm_signature_status(
                    &hot.rpc_fallback_url,
                    &signature,
                    parse_env_u64("RUST_SELECTED_CANDIDATE_BUY_STATUS_TIMEOUT_MS", 2_000),
                    parse_env_u64("RUST_SELECTED_CANDIDATE_BUY_STATUS_POLL_MS", 100),
                )
                .await;
                let finality_error_kind =
                    selected_candidate_buy_finality_error_kind(finality.error.as_deref());
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateBuyFinality",
                        "atMs": now_ms(),
                        "key": candidate.key,
                        "mint": candidate.mint,
                        "signature": signature,
                        "status": finality.status,
                        "confirmationStatus": finality.confirmation_status,
                        "slot": finality.slot,
                        "error": finality.error,
                        "buyResultToFinalityMs": now_ms().saturating_sub(buy_result_at_ms),
                        "selectedToFinalityMs": started.elapsed().as_millis(),
                        "finalityAuditOnly": true,
                    }),
                );
                if finality_error_kind == "buy_finality_custom_6042" {
                    emit_selected_candidate_6042_counterfactual(
                        cfg,
                        hot,
                        curve_cache,
                        &candidate,
                        &min_out_plan,
                        &signature,
                        tokens_out_raw,
                        sol_in_lamports,
                        finality.slot,
                    )
                    .await;
                }
                if finality.ok == Some(false) {
                    let cancel_status =
                        registry.cancel(exit_watcher::CancelPumpfunExitWatchCommand {
                            id: format!(
                                "rust-selected-cancel-failed-buy-{}-{}",
                                now_ms(),
                                candidate.mint
                            ),
                            mint: candidate.mint.clone(),
                            reason: Some(format!(
                                "buy finalized failed: {}",
                                finality
                                    .error
                                    .clone()
                                    .unwrap_or_else(|| "unknown".to_string())
                            )),
                        });
                    let accounting_bucket =
                        selected_candidate_buy_failure_accounting_bucket(finality_error_kind);
                    let optimistic_exit_was_active = exit_watch_activated.load(Ordering::SeqCst);
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateProtectedMissedFill",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "signature": signature,
                            "reason": finality_error_kind,
                            "accountingBucket": accounting_bucket,
                            "liveTrade": false,
                            "strategyLoss": false,
                            "protectedMiss": finality_error_kind == "buy_finality_custom_6042",
                            "buyBroadcastOkButFinalityFailed": true,
                            "optimisticExitWasActive": optimistic_exit_was_active,
                            "earlyBalanceProofSeen": early_balance_proved.load(Ordering::SeqCst),
                            "cancelOk": cancel_status.ok,
                            "cancelPhase": cancel_status.phase,
                            "cancelActionPending": cancel_status.action_pending,
                            "cancelError": cancel_status.error.clone(),
                            "status": finality.status,
                            "confirmationStatus": finality.confirmation_status,
                            "error": finality.error.clone(),
                            "minOutPlan": min_out_plan.clone(),
                            "quotedTokensOutRaw": tokens_out_raw,
                            "safeSellTokensRaw": safe_sell_tokens_raw,
                            "note": "Buy broadcast returned a signature, but finalized failed; classify as missed/protected entry, not a landed trade PnL row.",
                        }),
                    );
                    if optimistic_exit_was_active {
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidateOptimisticExitInvalidatedByFailedBuy",
                                "atMs": now_ms(),
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "signature": signature,
                                "reason": finality_error_kind,
                                "accountingBucket": accounting_bucket,
                                "liveTrade": false,
                                "strategyLoss": false,
                                "cancelOk": cancel_status.ok,
                                "cancelPhase": cancel_status.phase,
                                "cancelActionPending": cancel_status.action_pending,
                                "cancelError": cancel_status.error.clone(),
                                "note": "Any pre-proof sell attempt for this mint belongs to a buy that did not land and must not be scored as a real exit.",
                            }),
                        );
                    }
                    release_selected_candidate_terminal_failed_attempt(
                        cfg,
                        hot,
                        &candidate,
                        sol_in_lamports,
                    );
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateBuyFailedReleased",
                            "atMs": now_ms(),
                            "key": candidate.key,
                            "mint": candidate.mint,
                            "signature": signature,
                            "reason": finality_error_kind,
                            "accountingBucket": accounting_bucket,
                            "liveTrade": false,
                            "strategyLoss": false,
                            "protectedMiss": finality_error_kind == "buy_finality_custom_6042",
                            "terminalNoRetry": true,
                            "status": finality.status,
                            "confirmationStatus": finality.confirmation_status,
                            "error": finality.error,
                        }),
                    );
                } else {
                    let early_balance_proof_seen = early_balance_proved.load(Ordering::SeqCst);
                    let mut activate_exit_watch = false;
                    let mut defer_await_proof = false;
                    let mut cancel_missing_position_reason: Option<&'static str> = None;
                    if early_balance_proof_seen {
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidatePostBuyBalanceSkipped",
                                "atMs": now_ms(),
                                "mint": candidate.mint,
                                "reason": "early_balance_activated",
                            }),
                        );
                    } else {
                        let mut post_buy_balance_source = "yellowstone-token-balance-cache";
                        let post_buy_balance_started = Instant::now();
                        let post_buy_cached = if cfg.balance_cache_enabled {
                            wait_selected_candidate_cached_exact_token_balance(
                                cfg,
                                &candidate,
                                cfg.balance_cache_wait_ms.min(250),
                            )
                            .await
                        } else {
                            None
                        };
                        let post_buy_balance = if let Some(cached) = post_buy_cached {
                            emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidatePostBuyBalanceCacheHit",
                                    "atMs": now_ms(),
                                    "mint": candidate.mint,
                                    "ata": cached.ata,
                                    "slot": cached.slot,
                                    "observedAtMs": cached.observed_at_ms,
                                    "amountRaw": cached.amount_raw,
                                    "exact": cached.exact,
                                    "balanceSource": cached.source,
                                    "elapsedMs": post_buy_balance_started.elapsed().as_millis(),
                                }),
                            );
                            Ok(Some(cached.amount_raw))
                        } else if cfg.balance_cache_rpc_fallback_enabled {
                            post_buy_balance_source = "rpc-getTokenAccountBalance";
                            fetch_selected_candidate_token_balance_raw(
                                &hot.rpc_fallback_url,
                                &candidate.mint,
                                candidate.token_program_id.as_deref(),
                                parse_env_u64(
                                    "RUST_SELECTED_CANDIDATE_POST_BUY_BALANCE_TIMEOUT_MS",
                                    300,
                                )
                                .clamp(75, 2_000),
                            )
                            .await
                        } else {
                            Ok(None)
                        };
                        match post_buy_balance {
                            Ok(Some(actual_tokens_out_raw)) => {
                                if actual_tokens_out_raw == 0 {
                                    emit_selected_candidate_sniper_event(
                                        cfg,
                                        json!({
                                            "kind": "rustSelectedCandidatePostBuyBalanceMissing",
                                            "atMs": now_ms(),
                                            "mint": candidate.mint,
                                            "reason": "zero_balance",
                                            "balanceSource": post_buy_balance_source,
                                            "quotedTokensOutRaw": tokens_out_raw,
                                        }),
                                    );
                                    if finality.ok.is_none() {
                                        defer_await_proof = true;
                                        mark_selected_candidate_pending_exact_proof(
                                            cfg,
                                            &candidate.mint,
                                        );
                                        emit_selected_candidate_sniper_event(
                                            cfg,
                                            json!({
                                                "kind": "rustSelectedCandidatePositionProofDeferred",
                                                "atMs": now_ms(),
                                                "key": candidate.key,
                                                "mint": candidate.mint,
                                                "signature": signature,
                                                "reason": "zero_balance_after_status_timeout",
                                                "status": finality.status,
                                                "confirmationStatus": finality.confirmation_status,
                                                "note": "exit watch remains inert until exact wallet reconcile proves a sellable position",
                                            }),
                                        );
                                    } else {
                                        activate_exit_watch = false;
                                        cancel_missing_position_reason = Some("zero_balance");
                                    }
                                } else {
                                    let status = registry.update(
                                        exit_watcher::UpdatePumpfunExitWatchCommand {
                                            id: format!(
                                                "rust-selected-update-exact-balance-{}-{}",
                                                now_ms(),
                                                candidate.mint
                                            ),
                                            mint: candidate.mint.clone(),
                                            tokens_held_raw: actual_tokens_out_raw,
                                            cost_basis_lamports: sol_in_lamports,
                                            exact_proof: true,
                                            proof_source: Some(post_buy_balance_source.to_string()),
                                        },
                                    );
                                    emit_selected_candidate_sniper_event(
                                        cfg,
                                        json!({
                                            "kind": "rustSelectedCandidatePostBuyBalanceUpdate",
                                            "atMs": now_ms(),
                                            "mint": candidate.mint,
                                            "quotedTokensOutRaw": tokens_out_raw,
                                            "economicTokensHeldRaw": tokens_out_raw,
                                            "actualTokensOutRaw": actual_tokens_out_raw,
                                            "actualFillDegradationPct": selected_candidate_actual_fill_degradation_pct(
                                                tokens_out_raw,
                                                actual_tokens_out_raw,
                                            ),
                                            "impliedActualEntryMarketCapUsd": selected_candidate_implied_actual_entry_mc_usd(
                                                Some(candidate.entry_market_cap_usd),
                                                tokens_out_raw,
                                                actual_tokens_out_raw,
                                            ),
                                            "balanceSource": post_buy_balance_source,
                                            "ok": status.ok,
                                            "phase": status.phase,
                                            "error": status.error,
                                        }),
                                    );
                                    if cfg.post_buy_reject_fill_below_protected_min {
                                        if let Some(protected_min_tokens_out_raw) =
                                            selected_candidate_protected_min_tokens_out_raw(
                                                cfg,
                                                &candidate,
                                                effective_max_entry_drift_bps,
                                            )
                                        {
                                            let protected_floor_tokens_out_raw = json_u64_at(
                                                &min_out_plan,
                                                &["protectedFloorTokensOutRaw"],
                                            );
                                            let submitted_min_tokens_out_raw = json_u64_at(
                                                &min_out_plan,
                                                &["submittedMinTokensOutRaw"],
                                            );
                                            let post_buy_fill_floor_tokens_out_raw =
                                                submitted_min_tokens_out_raw
                                                    .or(protected_floor_tokens_out_raw)
                                                    .unwrap_or(protected_min_tokens_out_raw);
                                            if actual_tokens_out_raw
                                                < post_buy_fill_floor_tokens_out_raw
                                            {
                                                emit_selected_candidate_sniper_event(
                                                    cfg,
                                                    json!({
                                                        "kind": "rustSelectedCandidatePostBuyBadFillFlatten",
                                                        "atMs": now_ms(),
                                                        "key": candidate.key,
                                                        "mint": candidate.mint,
                                                        "actualTokensOutRaw": actual_tokens_out_raw,
                                                        "protectedMinTokensOutRaw": protected_min_tokens_out_raw,
                                                        "protectedFloorTokensOutRaw": protected_floor_tokens_out_raw,
                                                        "submittedMinTokensOutRaw": submitted_min_tokens_out_raw,
                                                        "postBuyFillFloorTokensOutRaw": post_buy_fill_floor_tokens_out_raw,
                                                        "quotedTokensOutRaw": tokens_out_raw,
                                                        "reason": "actual_fill_below_fee_aware_floor",
                                                    }),
                                                );
                                                let (flat_status, flat_action) = registry.source_flat(
                                                    exit_watcher::SourceFlatPumpfunExitWatchCommand {
                                                        id: format!(
                                                            "rust-selected-bad-fill-flatten-{}-{}",
                                                            now_ms(),
                                                            candidate.mint
                                                        ),
                                                        mint: candidate.mint.clone(),
                                                    },
                                                    now_ms().min(u64::MAX as u128) as u64,
                                                );
                                                emit_selected_candidate_sniper_event(
                                                    cfg,
                                                    json!({
                                                        "kind": "rustSelectedCandidatePostBuyBadFillFlattenStatus",
                                                        "atMs": now_ms(),
                                                        "key": candidate.key,
                                                        "mint": candidate.mint,
                                                        "ok": flat_status.ok,
                                                        "phase": flat_status.phase,
                                                        "actionPending": flat_status.action_pending,
                                                        "error": flat_status.error,
                                                    }),
                                                );
                                                if let Some(action) = flat_action {
                                                    let _ = action_tx.send(action);
                                                }
                                            }
                                        }
                                    }
                                    activate_exit_watch = status.ok;
                                }
                            }
                            Ok(None) => {
                                emit_selected_candidate_sniper_event(
                                    cfg,
                                    json!({
                                        "kind": "rustSelectedCandidatePostBuyBalanceMissing",
                                        "atMs": now_ms(),
                                        "mint": candidate.mint,
                                        "reason": "token_account_missing",
                                        "balanceSource": post_buy_balance_source,
                                        "quotedTokensOutRaw": tokens_out_raw,
                                    }),
                                );
                                defer_await_proof = true;
                                mark_selected_candidate_pending_exact_proof(cfg, &candidate.mint);
                                emit_selected_candidate_sniper_event(
                                    cfg,
                                    json!({
                                        "kind": "rustSelectedCandidatePositionProofDeferred",
                                        "atMs": now_ms(),
                                        "key": candidate.key,
                                        "mint": candidate.mint,
                                        "signature": signature,
                                        "reason": if finality.ok == Some(true) {
                                            "token_account_missing_after_landed_buy"
                                        } else {
                                            "token_account_missing_after_status_timeout"
                                        },
                                        "status": finality.status,
                                        "confirmationStatus": finality.confirmation_status,
                                        "note": "exit watch remains inert until exact wallet reconcile proves a sellable position",
                                    }),
                                );
                            }
                            Err(err) => {
                                emit_selected_candidate_sniper_event(
                                    cfg,
                                    json!({
                                        "kind": "rustSelectedCandidatePostBuyBalanceError",
                                        "atMs": now_ms(),
                                        "mint": candidate.mint,
                                        "error": err.to_string(),
                                        "balanceSource": post_buy_balance_source,
                                        "quotedTokensOutRaw": tokens_out_raw,
                                    }),
                                );
                                defer_await_proof = true;
                                mark_selected_candidate_pending_exact_proof(cfg, &candidate.mint);
                                emit_selected_candidate_sniper_event(
                                    cfg,
                                    json!({
                                        "kind": "rustSelectedCandidatePositionProofDeferred",
                                        "atMs": now_ms(),
                                        "key": candidate.key,
                                        "mint": candidate.mint,
                                        "signature": signature,
                                        "reason": if finality.ok == Some(true) {
                                            "post_buy_balance_error_after_landed_buy"
                                        } else {
                                            "post_buy_balance_error_after_status_timeout"
                                        },
                                        "status": finality.status,
                                        "confirmationStatus": finality.confirmation_status,
                                        "error": err.to_string(),
                                        "note": "exit watch remains inert until exact wallet reconcile proves a sellable position",
                                    }),
                                );
                            }
                        }
                    }
                    match selected_candidate_post_finality_exit_action(
                        exit_watch_activated.load(Ordering::SeqCst),
                        activate_exit_watch,
                        defer_await_proof,
                        early_balance_proof_seen,
                    ) {
                        SelectedCandidatePostFinalityExitAction::KeepAlreadyActivated => {}
                        SelectedCandidatePostFinalityExitAction::Activate => {
                            let activate_status = activate_selected_candidate_exit_watch(
                                cfg, registry, action_tx, &candidate,
                            );
                            if activate_status.ok {
                                exit_watch_activated.store(true, Ordering::SeqCst);
                            } else {
                                emit_selected_candidate_sniper_event(
                                    cfg,
                                    json!({
                                        "kind": "rustSelectedCandidateExitWatchActivationFailed",
                                        "atMs": now_ms(),
                                        "key": candidate.key,
                                        "mint": candidate.mint,
                                        "source": "post_finality",
                                        "phase": activate_status.phase,
                                        "error": activate_status.error,
                                    }),
                                );
                            }
                        }
                        SelectedCandidatePostFinalityExitAction::DeferAwaitProof => {
                            let active_exit_watch_cancel_status = if exit_watch_activated
                                .load(Ordering::SeqCst)
                            {
                                let cancel_status =
                                    registry.cancel(exit_watcher::CancelPumpfunExitWatchCommand {
                                        id: format!(
                                            "rust-selected-defer-cancel-active-watch-{}-{}",
                                            now_ms(),
                                            candidate.mint
                                        ),
                                        mint: candidate.mint.clone(),
                                        reason: Some("awaiting_exact_balance_proof".to_string()),
                                    });
                                exit_watch_activated.store(false, Ordering::SeqCst);
                                Some(cancel_status)
                            } else {
                                None
                            };
                            emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateExitActivationDeferred",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "signature": signature,
                                    "reason": "awaiting_exact_balance_proof",
                                    "status": finality.status,
                                    "confirmationStatus": finality.confirmation_status,
                                    "activeExitWatchCanceled": active_exit_watch_cancel_status.as_ref().map(|status| status.ok),
                                    "activeExitWatchCancelPhase": active_exit_watch_cancel_status.as_ref().map(|status| status.phase),
                                    "activeExitWatchCancelError": active_exit_watch_cancel_status.as_ref().and_then(|status| status.error.clone()),
                                }),
                            );
                        }
                        SelectedCandidatePostFinalityExitAction::CancelMissingPosition => {
                            let reason =
                                cancel_missing_position_reason.unwrap_or("buy_not_confirmed");
                            let _ = registry.cancel(exit_watcher::CancelPumpfunExitWatchCommand {
                                id: format!(
                                    "rust-selected-cancel-missing-position-{}-{}",
                                    now_ms(),
                                    candidate.mint
                                ),
                                mint: candidate.mint.clone(),
                                reason: Some(reason.to_string()),
                            });
                            release_selected_candidate_terminal_failed_attempt(
                                cfg,
                                hot,
                                &candidate,
                                sol_in_lamports,
                            );
                            emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateBuyNoPositionReleased",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "signature": signature,
                                    "reason": reason,
                                    "terminalNoRetry": true,
                                    "status": finality.status,
                                    "confirmationStatus": finality.confirmation_status,
                                    "error": finality.error,
                                }),
                            );
                        }
                    }
                    if finality.ok == Some(true) && !exit_watch_registered {
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidateNoExitWatchFlatten",
                                "atMs": now_ms(),
                                "key": candidate.key,
                                "mint": candidate.mint,
                                "reason": "landed_buy_without_exit_watch",
                            }),
                        );
                        match flatten_selected_candidate_wallet_mint(cfg, hot, &candidate.mint)
                            .await
                        {
                            Ok(flattened) => emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateNoExitWatchFlattenDone",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "flattened": flattened,
                                }),
                            ),
                            Err(err) => emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateNoExitWatchFlattenError",
                                    "atMs": now_ms(),
                                    "key": candidate.key,
                                    "mint": candidate.mint,
                                    "error": err.to_string(),
                                }),
                            ),
                        }
                    }
                }
            }
        }
        Ok(response) => {
            release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
            let buy_result_at_ms = now_ms();
            let first_send_at_ms = response.first_send_elapsed_ms.map(|elapsed| {
                u128::from(candidate_selected_at_ms).saturating_add(elapsed)
            });
            let first_ack_at_ms = response.first_ack_elapsed_ms.map(|elapsed| {
                u128::from(candidate_selected_at_ms).saturating_add(elapsed)
            });
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateBuyResult",
                    "atMs": buy_result_at_ms,
                    "candidateSelectedAtMs": candidate_selected_at_ms,
                    "immediateYellowstoneCheckAtMs": immediate_yellowstone_check_at_ms,
                    "authoritativeStateReadyAtMs": authoritative_state_ready_at_ms,
                    "finalGuardCompleteAtMs": final_guard_complete_at_ms,
                    "firstSendAtMs": first_send_at_ms,
                    "firstAckAtMs": first_ack_at_ms,
                    "buyResultAtMs": buy_result_at_ms,
                    "selectedToImmediateCurveCheckMs": immediate_yellowstone_check_at_ms.map(|check_at| {
                        check_at.saturating_sub(candidate_selected_at_ms)
                    }),
                    "selectedToAuthoritativeReadyMs": authoritative_state_ready_at_ms.map(|ready_at| {
                        ready_at.saturating_sub(candidate_selected_at_ms)
                    }),
                    "selectedToFirstSendMs": response.first_send_elapsed_ms,
                    "authoritativeReadyToFirstSendMs": authoritative_state_ready_at_ms
                        .zip(first_send_at_ms)
                        .map(|(ready_at, send_at)| send_at.saturating_sub(u128::from(ready_at))),
                    "ok": false,
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "error": response.error.unwrap_or_else(|| "Rust executor failed".to_string()),
                    "elapsedMs": response.elapsed_ms,
                    "buildMs": response.build_ms,
                    "signMs": response.sign_ms,
                    "serializeMs": response.serialize_ms,
                    "firstSendElapsedMs": response.first_send_elapsed_ms,
                    "firstAckElapsedMs": response.first_ack_elapsed_ms,
                    "broadcastMs": response.broadcast_ms,
                    "broadcastFirstAckMs": response.broadcast_first_ack_ms,
                    "broadcastCandidateCount": response.broadcast_candidate_count,
                    "priorityGraceMs": response.priority_grace_ms,
                    "priorityGraceDetached": response.priority_grace_detached,
                    "txBytes": response.tx_bytes,
                    "selectedToBuyResultMs": started.elapsed().as_millis(),
                }),
            );
        }
        Err(err) => {
            release_selected_candidate_reservations(cfg, hot, &candidate, cfg.buy_lamports);
            let buy_result_at_ms = now_ms();
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateBuyResult",
                    "atMs": buy_result_at_ms,
                    "candidateSelectedAtMs": candidate_selected_at_ms,
                    "immediateYellowstoneCheckAtMs": immediate_yellowstone_check_at_ms,
                    "authoritativeStateReadyAtMs": authoritative_state_ready_at_ms,
                    "finalGuardCompleteAtMs": final_guard_complete_at_ms,
                    "buyResultAtMs": buy_result_at_ms,
                    "selectedToImmediateCurveCheckMs": immediate_yellowstone_check_at_ms.map(|check_at| {
                        check_at.saturating_sub(candidate_selected_at_ms)
                    }),
                    "selectedToAuthoritativeReadyMs": authoritative_state_ready_at_ms.map(|ready_at| {
                        ready_at.saturating_sub(candidate_selected_at_ms)
                    }),
                    "ok": false,
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "error": err.to_string(),
                    "selectedToBuyResultMs": started.elapsed().as_millis(),
                }),
            );
        }
    }
}

#[derive(Debug, Clone)]
struct SelectedCandidate {
    key: String,
    mint: String,
    source_signature: String,
    source_wallet: String,
    entry_market_cap_usd: f64,
    event_lag_ms: u128,
    bonding_curve: String,
    creator: String,
    token_program_id: Option<String>,
    quote_mint: Option<String>,
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    shadow_tokens_out_raw: u64,
    shadow_sol_in_lamports: u64,
    reserve_slot: Option<u64>,
    reserve_observed_at_ms: Option<u64>,
    reserve_source: &'static str,
    native_features: Option<Value>,
    source_ix_accounts: Option<Vec<String>>,
    source_ix_data_base64: Option<String>,
}

async fn selected_candidate_from_raw_candidate(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    row: &Value,
) -> Result<Option<SelectedCandidate>> {
    let schema = json_string_at(row, &["schemaVersion"]);
    if schema.as_deref() != Some("two-cq-lead-attribution-candidate-1") {
        if matches!(
            schema.as_deref(),
            Some("two-cq-lead-attribution-mayhem-block-1")
        ) {
            selected_candidate_reject(
                cfg,
                "raw_candidate_mayhem_block_schema",
                json!({ "mint": json_string_at(row, &["mint"]) }),
            );
        }
        return Ok(None);
    }
    let mint = json_string_at(row, &["mint"])
        .or_else(|| json_string_at(row, &["features", "mint"]))
        .context("raw candidate missing mint")?;
    let key = json_string_at(row, &["canonicalCandidateId"])
        .or_else(|| json_string_at(row, &["features", "canonicalCandidateId"]))
        .or_else(|| json_string_at(row, &["key"]))
        .unwrap_or_else(|| mint.clone());
    if cfg.reject_mayhem_retry_promoted
        && (json_bool_at(row, &["mayhemRetryPromoted"]).unwrap_or(false)
            || json_bool_at(row, &["features", "mayhemRetryPromoted"]).unwrap_or(false))
    {
        selected_candidate_reject(
            cfg,
            "raw_candidate_mayhem_retry_promoted",
            json!({ "key": key, "mint": mint }),
        );
        return Ok(None);
    }
    if cfg.reject_weak_mayhem_recovered
        && (json_bool_at(row, &["isWeakMayhemRecovered"]).unwrap_or(false)
            || json_bool_at(row, &["features", "isWeakMayhemRecovered"]).unwrap_or(false)
            || json_string_at(row, &["mayhemBlockReason"]).as_deref()
                == Some("mayhem_source_check_unavailable")
            || json_string_at(row, &["features", "mayhemBlockReason"]).as_deref()
                == Some("mayhem_source_check_unavailable"))
    {
        selected_candidate_reject(
            cfg,
            "raw_candidate_weak_mayhem_recovered_veto",
            json!({
                "key": key,
                "mint": mint,
                "isWeakMayhemRecovered": json_bool_at(row, &["isWeakMayhemRecovered"])
                    .or_else(|| json_bool_at(row, &["features", "isWeakMayhemRecovered"])),
                "mayhemBlockReason": json_string_at(row, &["mayhemBlockReason"])
                    .or_else(|| json_string_at(row, &["features", "mayhemBlockReason"])),
            }),
        );
        return Ok(None);
    }
    let row_profile = json_string_at(row, &["profile"]).unwrap_or_default();
    let feature_profile = json_string_at(row, &["features", "profile"]).unwrap_or_default();
    let matched_profiles = json_string_array_at(row, &["matchedProfiles"]).unwrap_or_default();
    let is_allowed_source_profile = |profile: &str| {
        profile == "mc_expansion_strong_flow_watch"
            || profile == "token2022_expansion_strong_flow"
            || profile == "rust_native_strict_age500_fb4000_mc3k_no_midnet_v2"
    };
    if !is_allowed_source_profile(&row_profile)
        && !is_allowed_source_profile(&feature_profile)
        && !matched_profiles
            .iter()
            .any(|profile| is_allowed_source_profile(profile))
    {
        return Ok(None);
    }
    let raw_relax_pre_gate_for_proof = parse_env_bool(
        "RUST_SELECTED_CANDIDATE_RAW_RELAX_PRE_GATE_FOR_PROOF",
        false,
    );
    let token_program = json_string_at(row, &["features", "tokenProgram"])
        .or_else(|| json_string_at(row, &["features", "creationKind"]))
        .unwrap_or_default();
    if !raw_relax_pre_gate_for_proof
        && token_program != "token_2022"
        && token_program != "token2022"
    {
        return Ok(None);
    }
    let source_ix_accounts_pre = json_string_array_at(row, &["features", "sourceIxAccounts"])
        .or_else(|| json_string_array_at(row, &["sourceIxAccounts"]));
    if let Some(accounts) = source_ix_accounts_pre.as_ref() {
        let mayhem_account = env::var("MAYHEM_SKIP_ACCOUNT")
            .ok()
            .filter(|value| !value.trim().is_empty())
            .unwrap_or_else(|| "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e".to_string());
        if accounts.iter().any(|account| account == &mayhem_account) {
            selected_candidate_reject(
                cfg,
                "raw_candidate_mayhem_source_ix_account",
                json!({ "key": key, "mint": mint, "mayhemAccount": mayhem_account }),
            );
            return Ok(None);
        }
    }
    let bonding_curve_pre = source_ix_accounts_pre
        .as_ref()
        .and_then(|accounts| source_bonding_curve_for_buy(&mint, accounts));
    if cfg.curve_cache_enabled {
        if let Some(bonding_curve) = bonding_curve_pre.as_deref() {
            curve_cache.watch(bonding_curve);
            let raw_virtual_sol_reserves = json_u64_at(row, &["features", "virtualSolReserves"])
                .or_else(|| json_u64_at(row, &["virtualSolReserves"]));
            let raw_virtual_token_reserves =
                json_u64_at(row, &["features", "virtualTokenReserves"])
                    .or_else(|| json_u64_at(row, &["virtualTokenReserves"]));
            if let (Some(virtual_sol_reserves), Some(virtual_token_reserves)) =
                (raw_virtual_sol_reserves, raw_virtual_token_reserves)
            {
                let slot = json_u64_at(row, &["features", "curveMarkSlot"])
                    .or_else(|| json_u64_at(row, &["curveMarkSlot"]))
                    .or_else(|| json_u64_at(row, &["features", "sourceSlot"]))
                    .or_else(|| json_u64_at(row, &["sourceSlot"]))
                    .unwrap_or(0);
                let observed_at_ms = json_u64_at(row, &["features", "curveObservedAtMs"])
                    .or_else(|| json_u64_at(row, &["curveObservedAtMs"]))
                    .or_else(|| json_u64_at(row, &["features", "observedAtMs"]))
                    .or_else(|| json_u64_at(row, &["entryObservedAtMs"]))
                    .unwrap_or_else(|| now_ms().min(u64::MAX as u128) as u64);
                curve_cache.update_from_source(
                    bonding_curve.to_string(),
                    CurveState {
                        virtual_token_reserves,
                        virtual_sol_reserves,
                        token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
                        complete: false,
                        creator: None,
                    },
                    slot,
                    observed_at_ms,
                    "raw-candidate-feature-snapshot",
                );
            }
        }
    }
    let entry_at_ms = json_u64_at(row, &["entryObservedAtMs"])
        .or_else(|| json_u64_at(row, &["features", "observedAtMs"]))
        .or_else(|| json_string_at(row, &["at"]).and_then(|at| parse_iso_ms(&at)))
        .context("raw candidate missing observedAtMs")?;
    let event_lag_ms = now_ms().saturating_sub(entry_at_ms as u128);
    if event_lag_ms > cfg.max_event_lag_ms as u128 {
        selected_candidate_reject(
            cfg,
            "raw_candidate_stale",
            json!({ "key": key, "mint": mint, "eventLagMs": event_lag_ms }),
        );
        return Ok(None);
    }
    let entry_mc = json_f64_at(row, &["entryMarketCapUsd"])
        .or_else(|| json_f64_at(row, &["features", "marketCapUsd"]))
        .context("raw candidate missing entry MC")?;
    let net_buy = json_f64_at(row, &["features", "netBuySol60"]).unwrap_or(f64::NAN);
    let buy_count = json_f64_at(row, &["features", "buyCount60"]).unwrap_or(f64::NAN);
    let sell_pressure = json_f64_at(row, &["features", "sellPressure60"]).unwrap_or(f64::NAN);
    let dominant = json_f64_at(row, &["features", "dominantBuyShare60"]).unwrap_or(f64::NAN);
    let unique_wallets = json_f64_at(row, &["features", "uniqueWallets60"]).unwrap_or(f64::NAN);
    let unique_per_buy = json_f64_at(row, &["features", "uniquePerBuy60"]).unwrap_or(f64::NAN);
    let buy_count_1000 = json_f64_at(row, &["features", "buyCount1000ms"]).unwrap_or(f64::NAN);
    let inter_buy_median = json_f64_at(row, &["features", "interBuyMedianMs"]).unwrap_or(f64::NAN);
    let first_buyer_bps =
        json_f64_at(row, &["features", "firstBuyerQuoteShareBps"]).unwrap_or(f64::NAN);
    let creation_age = json_f64_at(row, &["features", "creationAgeMs"]).unwrap_or(f64::NAN);
    let pre_gate = entry_mc >= 5_000.0
        && entry_mc <= 8_000.0
        && net_buy >= 3.0
        && net_buy <= 12.0
        && !(net_buy >= 5.0 && net_buy < 7.0)
        && buy_count >= 3.0
        && buy_count <= 15.0
        && sell_pressure <= 0.02
        && dominant <= 0.4
        && unique_wallets >= 3.0
        && unique_per_buy >= 0.7
        && buy_count_1000 >= 3.0
        && inter_buy_median <= 10.0
        && first_buyer_bps <= 4_000.0
        && creation_age.is_finite()
        && creation_age >= 500.0;
    if !pre_gate && !raw_relax_pre_gate_for_proof {
        return Ok(None);
    }

    let source_ix_accounts =
        source_ix_accounts_pre.context("raw candidate missing sourceIxAccounts")?;
    let bonding_curve = bonding_curve_pre
        .context("raw candidate source accounts missing Pump.fun bonding curve")?;
    let curve_snapshot =
        selected_candidate_curve_snapshot(cfg, hot, curve_cache, &key, &mint, &bonding_curve)
            .await?;
    let state = curve_snapshot.state.clone();
    if state.complete {
        selected_candidate_reject(
            cfg,
            "raw_candidate_curve_complete",
            json!({ "key": key, "mint": mint, "bondingCurve": bonding_curve }),
        );
        return Ok(None);
    }
    let sol_price = parse_env_optional_f64("RUST_SELECTED_CANDIDATE_SOL_PRICE_USD")
        .or(hot.sol_price_usd)
        .or_else(|| parse_env_optional_f64("SOL_PRICE_USD"))
        .or_else(|| parse_env_optional_f64("LOCAL_PUMPFUN_SOL_PRICE_USD"))
        .or_else(|| parse_env_optional_f64("SHRED_MC_SOL_PRICE_USD"))
        .unwrap_or(160.0);
    let direct_mc_sol =
        estimate_market_cap_sol(&state).context("raw candidate direct MC unavailable")?;
    let direct_mc_usd = direct_mc_sol * sol_price;
    let raw_max_direct_mc_usd =
        parse_env_optional_f64("RUST_SELECTED_CANDIDATE_RAW_MAX_DIRECT_MC_USD").unwrap_or(3_000.0);
    if direct_mc_usd > raw_max_direct_mc_usd {
        selected_candidate_reject(
            cfg,
            "raw_candidate_direct_mc_gt_cap",
            json!({
                "key": key,
                "mint": mint,
                "entryMarketCapUsd": entry_mc,
                "directEntryMarketCapUsd": direct_mc_usd,
                "maxDirectEntryMarketCapUsd": raw_max_direct_mc_usd,
            }),
        );
        return Ok(None);
    }
    let token_program_id_hint = json_string_at(row, &["features", "tokenProgramId"])
        .or_else(|| json_string_at(row, &["tokenProgramId"]))
        .or_else(|| {
            json_string_at(
                row,
                &["directEntryQuote", "curveSnapshot", "tokenProgramId"],
            )
        });
    let effective_token2022 = selected_candidate_effective_token2022(
        token_program.as_str(),
        token_program_id_hint.as_deref(),
        &source_ix_accounts,
    );
    let creator = state
        .creator
        .clone()
        .or_else(|| json_string_at(row, &["features", "curveCreator"]))
        .or_else(|| json_string_at(row, &["curveCreator"]))
        .or_else(|| {
            if raw_relax_pre_gate_for_proof && effective_token2022 {
                Some("source-template-only".to_string())
            } else {
                None
            }
        })
        .context("raw candidate curve missing creator")?;
    let quote_tokens = quote_buy_tokens_out_raw_main(
        state.virtual_sol_reserves as u128,
        state.virtual_token_reserves as u128,
        cfg.buy_lamports as u128,
    )?;
    let token_program_id = if effective_token2022 {
        Some(TOKEN_2022_PROGRAM_ID.to_string())
    } else {
        Some(TOKEN_PROGRAM_ID.to_string())
    };
    Ok(Some(SelectedCandidate {
        key,
        mint,
        source_signature: json_string_at(row, &["sourceSignature"])
            .or_else(|| json_string_at(row, &["features", "sourceSignature"]))
            .unwrap_or_else(|| "raw-candidate-selector".to_string()),
        source_wallet: json_string_at(row, &["features", "wallet"])
            .or_else(|| json_string_at(row, &["features", "sourceWallet"]))
            .unwrap_or_else(|| "rust-raw-candidate-selector".to_string()),
        entry_market_cap_usd: direct_mc_usd,
        event_lag_ms,
        bonding_curve,
        creator,
        token_program_id,
        quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
        virtual_sol_reserves: state.virtual_sol_reserves,
        virtual_token_reserves: state.virtual_token_reserves,
        shadow_tokens_out_raw: u64::try_from(quote_tokens).context("quote tokens exceed u64")?,
        shadow_sol_in_lamports: cfg.buy_lamports,
        reserve_slot: curve_snapshot.slot,
        reserve_observed_at_ms: Some(curve_snapshot.observed_at_ms),
        reserve_source: curve_snapshot.source,
        native_features: None,
        source_ix_accounts: Some(source_ix_accounts),
        source_ix_data_base64: json_string_at(row, &["features", "sourceIxDataBase64"])
            .or_else(|| json_string_at(row, &["sourceIxDataBase64"])),
    }))
}

fn selected_candidate_effective_token2022(
    token_program_label: &str,
    token_program_id_hint: Option<&str>,
    source_ix_accounts: &[String],
) -> bool {
    match token_program_id_hint {
        Some(TOKEN_2022_PROGRAM_ID) => return true,
        Some(TOKEN_PROGRAM_ID) => return false,
        _ => {}
    }
    match token_program_label.trim().to_ascii_lowercase().as_str() {
        "token_2022" | "token2022" => true,
        "token" | "spl_token" | "legacy" => false,
        _ => source_ix_accounts
            .iter()
            .any(|account| account == TOKEN_2022_PROGRAM_ID),
    }
}

#[derive(Debug, Clone)]
struct SelectedCandidateCurveSnapshot {
    state: CurveState,
    slot: Option<u64>,
    observed_at_ms: u64,
    source: &'static str,
}

async fn selected_candidate_curve_snapshot(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    key: &str,
    mint: &str,
    bonding_curve: &str,
) -> Result<SelectedCandidateCurveSnapshot> {
    if cfg.curve_cache_enabled {
        curve_cache.watch(bonding_curve);
        if let Some(cached) =
            curve_cache.get_fresh_yellowstone(bonding_curve, cfg.curve_cache_max_age_ms)
        {
            let allowed = selected_candidate_final_guard_curve_source_allowed(cfg, &cached);
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateCurveCacheHit",
                    "atMs": now_ms(),
                    "key": key,
                    "mint": mint,
                    "bondingCurve": bonding_curve,
                    "slot": cached.slot,
                    "cacheSource": cached.source,
                    "sourcePreference": "yellowstone_first",
                    "independentCurveCache": selected_candidate_independent_curve_source(cached.source),
                    "cacheAccepted": allowed,
                    "cacheAgeMs": (now_ms().min(u64::MAX as u128) as u64).saturating_sub(cached.observed_at_ms),
                }),
            );
            if allowed {
                return Ok(SelectedCandidateCurveSnapshot {
                    state: cached.state,
                    slot: Some(cached.slot),
                    observed_at_ms: cached.observed_at_ms,
                    source: cached.source,
                });
            }
        }
        if let Some(cached) = curve_cache.get_fresh(bonding_curve, cfg.curve_cache_max_age_ms) {
            let independent = selected_candidate_independent_curve_source(cached.source);
            let accepted = selected_candidate_final_guard_curve_source_allowed(cfg, &cached);
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateCurveCacheHit",
                    "atMs": now_ms(),
                    "key": key,
                    "mint": mint,
                    "bondingCurve": bonding_curve,
                    "slot": cached.slot,
                    "cacheSource": cached.source,
                    "sourcePreference": "mixed_after_yellowstone",
                    "independentCurveCache": independent,
                    "cacheAccepted": accepted,
                    "cacheAgeMs": (now_ms().min(u64::MAX as u128) as u64).saturating_sub(cached.observed_at_ms),
                }),
            );
            if !accepted {
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateCurveCacheNonIndependentHit",
                        "atMs": now_ms(),
                        "key": key,
                        "mint": mint,
                        "bondingCurve": bonding_curve,
                        "slot": cached.slot,
                        "cacheSource": cached.source,
                        "sourcePreference": "mixed_after_yellowstone",
                        "fillFirstFinalGuardYellowstoneRequired": cfg.submit_enabled
                            && selected_candidate_fill_first_buy_policy(cfg)
                            && parse_env_bool(
                                "RUST_SELECTED_CANDIDATE_FILL_FIRST_FINAL_GUARD_YELLOWSTONE_REQUIRED",
                                true
                            ),
                        "cacheAgeMs": (now_ms().min(u64::MAX as u128) as u64).saturating_sub(cached.observed_at_ms),
                    }),
                );
            } else {
                return Ok(SelectedCandidateCurveSnapshot {
                    state: cached.state,
                    slot: Some(cached.slot),
                    observed_at_ms: cached.observed_at_ms,
                    source: cached.source,
                });
            }
        }
        let wait_started = Instant::now();
        while wait_started.elapsed() < Duration::from_millis(cfg.curve_cache_wait_ms) {
            sleep(Duration::from_millis(2)).await;
            if let Some(cached) =
                curve_cache.get_fresh_yellowstone(bonding_curve, cfg.curve_cache_max_age_ms)
            {
                let allowed = selected_candidate_final_guard_curve_source_allowed(cfg, &cached);
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateCurveCacheHit",
                        "atMs": now_ms(),
                        "key": key,
                        "mint": mint,
                        "bondingCurve": bonding_curve,
                        "slot": cached.slot,
                        "cacheSource": cached.source,
                        "sourcePreference": "yellowstone_first",
                        "independentCurveCache": selected_candidate_independent_curve_source(cached.source),
                        "cacheAccepted": allowed,
                        "cacheAgeMs": (now_ms().min(u64::MAX as u128) as u64).saturating_sub(cached.observed_at_ms),
                        "waitedMs": wait_started.elapsed().as_millis(),
                    }),
                );
                if allowed {
                    return Ok(SelectedCandidateCurveSnapshot {
                        state: cached.state,
                        slot: Some(cached.slot),
                        observed_at_ms: cached.observed_at_ms,
                        source: cached.source,
                    });
                }
            }
            if let Some(cached) = curve_cache.get_fresh(bonding_curve, cfg.curve_cache_max_age_ms) {
                let independent = selected_candidate_independent_curve_source(cached.source);
                let accepted = selected_candidate_final_guard_curve_source_allowed(cfg, &cached);
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateCurveCacheHit",
                        "atMs": now_ms(),
                        "key": key,
                        "mint": mint,
                        "bondingCurve": bonding_curve,
                        "slot": cached.slot,
                        "cacheSource": cached.source,
                        "sourcePreference": "mixed_after_yellowstone",
                        "independentCurveCache": independent,
                        "cacheAccepted": accepted,
                        "cacheAgeMs": (now_ms().min(u64::MAX as u128) as u64).saturating_sub(cached.observed_at_ms),
                        "waitedMs": wait_started.elapsed().as_millis(),
                    }),
                );
                if !accepted {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateCurveCacheNonIndependentHit",
                            "atMs": now_ms(),
                            "key": key,
                            "mint": mint,
                            "bondingCurve": bonding_curve,
                            "slot": cached.slot,
                            "cacheSource": cached.source,
                            "sourcePreference": "mixed_after_yellowstone",
                            "fillFirstFinalGuardYellowstoneRequired": cfg.submit_enabled
                                && selected_candidate_fill_first_buy_policy(cfg)
                                && parse_env_bool(
                                    "RUST_SELECTED_CANDIDATE_FILL_FIRST_FINAL_GUARD_YELLOWSTONE_REQUIRED",
                                    true
                                ),
                            "cacheAgeMs": (now_ms().min(u64::MAX as u128) as u64).saturating_sub(cached.observed_at_ms),
                            "waitedMs": wait_started.elapsed().as_millis(),
                        }),
                    );
                    continue;
                }
                return Ok(SelectedCandidateCurveSnapshot {
                    state: cached.state,
                    slot: Some(cached.slot),
                    observed_at_ms: cached.observed_at_ms,
                    source: cached.source,
                });
            }
        }
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateCurveCacheMiss",
                "atMs": now_ms(),
                "key": key,
                "mint": mint,
                "bondingCurve": bonding_curve,
                "waitedMs": wait_started.elapsed().as_millis(),
                "rpcFallbackEnabled": cfg.curve_rpc_fallback_enabled,
            }),
        );
    }
    if !cfg.curve_rpc_fallback_enabled {
        bail!("selected candidate curve cache miss and RPC fallback is disabled");
    }
    let account_data = fetch_account_data_base64(
        &hot.rpc_fallback_url,
        bonding_curve,
        parse_env_u64("RUST_SELECTED_CANDIDATE_CURVE_RPC_TIMEOUT_MS", 80).clamp(25, 500),
    )
    .await?;
    let state = decode_bonding_curve_state(&account_data)
        .context("raw candidate curve account decode failed")?;
    Ok(SelectedCandidateCurveSnapshot {
        state,
        slot: None,
        observed_at_ms: now_ms().min(u64::MAX as u128) as u64,
        source: "rpc-fallback",
    })
}

fn selected_candidate_selected_event_curve_snapshot(
    candidate: &SelectedCandidate,
) -> SelectedCandidateCurveSnapshot {
    SelectedCandidateCurveSnapshot {
        state: CurveState {
            virtual_token_reserves: candidate.virtual_token_reserves,
            virtual_sol_reserves: candidate.virtual_sol_reserves,
            token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
            complete: false,
            creator: Some(candidate.creator.clone()),
        },
        slot: candidate.reserve_slot,
        observed_at_ms: candidate
            .reserve_observed_at_ms
            .unwrap_or_else(|| now_ms().min(u64::MAX as u128) as u64),
        source: "selected-event-fallback",
    }
}

fn raw_candidate_selected_event(
    cfg: &SelectedCandidateSniperConfig,
    candidate: &SelectedCandidate,
) -> Value {
    json!({
        "kind": "rustRawCandidateSelected",
        "schemaVersion": "rust-selected-candidate-raw-selector-1",
        "atMs": now_ms(),
        "lane": cfg.required_lane,
        "policy": cfg.required_policy,
        "key": candidate.key,
        "mint": candidate.mint,
        "entryMarketCapUsd": candidate.entry_market_cap_usd,
        "eventLagMs": candidate.event_lag_ms,
        "reserveSource": candidate.reserve_source,
        "reserveSlot": candidate.reserve_slot,
        "reserveObservedAtMs": candidate.reserve_observed_at_ms,
        "bondingCurve": candidate.bonding_curve,
        "tokenProgramId": candidate.token_program_id,
    })
}

fn selected_candidate_from_row(
    cfg: &SelectedCandidateSniperConfig,
    curve_cache: &CandidateCurveCache,
    row: &Value,
) -> Option<SelectedCandidate> {
    if json_string_at(row, &["schemaVersion"]).as_deref()
        != Some("two-cq-continuous-shadow-event-1")
        || json_string_at(row, &["event"]).as_deref() != Some("candidate_selected")
    {
        selected_candidate_reject(cfg, "ignored_schema", json!({}));
        return None;
    }
    if let Some(required_lane) = &cfg.required_lane {
        if json_string_at(row, &["lane"]).as_deref() != Some(required_lane.as_str()) {
            selected_candidate_reject(
                cfg,
                "lane_mismatch",
                json!({ "lane": json_string_at(row, &["lane"]), "requiredLane": required_lane }),
            );
            return None;
        }
    }
    if !cfg.allowed_lanes.is_empty() {
        let lane = json_string_at(row, &["lane"]);
        let allowed = lane
            .as_ref()
            .map(|value| cfg.allowed_lanes.iter().any(|allowed| allowed == value))
            .unwrap_or(false);
        if !allowed {
            selected_candidate_reject(
                cfg,
                "lane_not_allowed",
                json!({ "lane": lane, "allowedLanes": cfg.allowed_lanes.clone() }),
            );
            return None;
        }
    }
    if let Some(required_policy) = &cfg.required_policy {
        if json_string_at(row, &["policy"]).as_deref() != Some(required_policy.as_str()) {
            selected_candidate_reject(
                cfg,
                "policy_mismatch",
                json!({ "policy": json_string_at(row, &["policy"]), "requiredPolicy": required_policy }),
            );
            return None;
        }
    }
    if cfg.reject_mayhem_retry_promoted
        && json_bool_at(row, &["mayhemRetryPromoted"]).unwrap_or(false)
    {
        selected_candidate_reject(cfg, "mayhem_retry_promoted", json!({}));
        return None;
    }
    let mint =
        json_string_at(row, &["mint"]).or_else(|| json_string_at(row, &["features", "mint"]))?;
    let key = json_string_at(row, &["key"])
        .or_else(|| json_string_at(row, &["features", "key"]))
        .unwrap_or_else(|| mint.clone());
    if cfg.reject_weak_mayhem_recovered
        && (json_bool_at(row, &["isWeakMayhemRecovered"]).unwrap_or(false)
            || json_bool_at(row, &["features", "isWeakMayhemRecovered"]).unwrap_or(false)
            || json_string_at(row, &["mayhemBlockReason"]).as_deref()
                == Some("mayhem_source_check_unavailable")
            || json_string_at(row, &["features", "mayhemBlockReason"]).as_deref()
                == Some("mayhem_source_check_unavailable"))
    {
        selected_candidate_reject(
            cfg,
            "weak_mayhem_recovered_veto",
            json!({
                "key": key,
                "mint": mint,
                "isWeakMayhemRecovered": json_bool_at(row, &["isWeakMayhemRecovered"])
                    .or_else(|| json_bool_at(row, &["features", "isWeakMayhemRecovered"])),
                "mayhemBlockReason": json_string_at(row, &["mayhemBlockReason"])
                    .or_else(|| json_string_at(row, &["features", "mayhemBlockReason"])),
            }),
        );
        return None;
    }
    let entry_at_ms = json_u64_at(row, &["entryAtMs"])
        .or_else(|| json_u64_at(row, &["features", "observedAtMs"]))?;
    let event_lag_ms = now_ms().saturating_sub(entry_at_ms as u128);
    if event_lag_ms > cfg.max_event_lag_ms as u128 {
        selected_candidate_reject(
            cfg,
            "stale_selected_candidate",
            json!({ "key": key, "mint": mint, "eventLagMs": event_lag_ms }),
        );
        return None;
    }
    let entry_market_cap_usd = json_f64_at(row, &["entryMarketCapUsd"])
        .or_else(|| json_f64_at(row, &["features", "marketCapUsd"]))?;
    if let Some(min_mc) = cfg.min_entry_mc_usd {
        if entry_market_cap_usd < min_mc {
            selected_candidate_reject(
                cfg,
                "entry_mc_below_min",
                json!({ "key": key, "mint": mint, "entryMarketCapUsd": entry_market_cap_usd, "minEntryMcUsd": min_mc }),
            );
            return None;
        }
    }
    if let Some(max_mc) = cfg.max_entry_mc_usd {
        if entry_market_cap_usd > max_mc {
            selected_candidate_reject(
                cfg,
                "entry_mc_above_max",
                json!({ "key": key, "mint": mint, "entryMarketCapUsd": entry_market_cap_usd, "maxEntryMcUsd": max_mc }),
            );
            return None;
        }
    }
    if json_string_at(row, &["directEntryQuote", "status"]).as_deref() != Some("ok") {
        selected_candidate_reject(
            cfg,
            "direct_entry_quote_unavailable",
            json!({ "key": key, "mint": mint }),
        );
        return None;
    }
    let bonding_curve = json_string_at(row, &["directEntryQuote", "curveSnapshot", "pda"])
        .or_else(|| json_string_at(row, &["directEntryQuote", "curveSnapshot", "bondingCurve"]))?;
    let creator = json_string_at(row, &["directEntryQuote", "curveSnapshot", "creator"])?;
    let token_program_id = json_string_at(
        row,
        &["directEntryQuote", "curveSnapshot", "tokenProgramId"],
    );
    let quote_mint = json_string_at(row, &["directEntryQuote", "curveSnapshot", "quoteMint"])
        .or_else(|| Some(WRAPPED_SOL_MINT.to_string()));
    let virtual_sol_reserves = json_u64_at(
        row,
        &["directEntryQuote", "curveSnapshot", "virtualSolReserves"],
    )?;
    let virtual_token_reserves = json_u64_at(
        row,
        &["directEntryQuote", "curveSnapshot", "virtualTokenReserves"],
    )?;
    let reserve_slot = json_u64_at(row, &["directEntryQuote", "curveSnapshot", "slot"]);
    let reserve_observed_at_ms =
        json_u64_at(row, &["directEntryQuote", "curveSnapshot", "observedAtMs"]);
    let reserve_source =
        if json_bool_at(row, &["adapter", "recoveredExecutionFields"]).unwrap_or(false) {
            "selected-event-recovered-native"
        } else {
            "selected-event"
        };
    if cfg.curve_cache_enabled {
        curve_cache.watch(&bonding_curve);
        let cache_creator = if Pubkey::from_str(&creator).is_ok() {
            Some(creator.clone())
        } else {
            None
        };
        curve_cache.update_from_source(
            bonding_curve.clone(),
            CurveState {
                virtual_token_reserves,
                virtual_sol_reserves,
                token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
                complete: false,
                creator: cache_creator,
            },
            reserve_slot.unwrap_or(0),
            reserve_observed_at_ms.unwrap_or_else(|| now_ms().min(u64::MAX as u128) as u64),
            reserve_source,
        );
    }
    let shadow_tokens_out_raw = json_u64_at(row, &["directEntryQuote", "tokensOutRaw"])?;
    let shadow_sol_in_lamports = json_u64_at(row, &["directEntryQuote", "solInLamports"])?;
    let source_signature = json_string_at(row, &["features", "sourceSignature"])
        .or_else(|| json_string_at(row, &["sourceSignature"]))
        .unwrap_or_else(|| key.clone());
    let source_wallet = json_string_at(row, &["features", "wallet"])
        .or_else(|| json_string_at(row, &["features", "sourceWallet"]))
        .unwrap_or_else(|| "rust-selected-candidate".to_string());
    Some(SelectedCandidate {
        key,
        mint,
        source_signature,
        source_wallet,
        entry_market_cap_usd,
        event_lag_ms,
        bonding_curve,
        creator,
        token_program_id,
        quote_mint,
        virtual_sol_reserves,
        virtual_token_reserves,
        shadow_tokens_out_raw,
        shadow_sol_in_lamports,
        reserve_slot,
        reserve_observed_at_ms,
        reserve_source,
        native_features: None,
        source_ix_accounts: json_string_array_at(row, &["features", "sourceIxAccounts"]),
        source_ix_data_base64: json_string_at(row, &["features", "sourceIxDataBase64"]),
    })
}

fn reserve_selected_candidate(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    candidate: &SelectedCandidate,
) -> Option<String> {
    {
        let mut keys = cfg.forwarded_keys.lock().ok()?;
        if keys.contains(&candidate.key) {
            return Some("duplicate_selected_key".to_string());
        }
        keys.insert(candidate.key.clone());
    }
    if cfg.submit_enabled {
        match hot.bought_signatures.lock() {
            Ok(mut seen) => {
                if seen.contains(&candidate.source_signature) {
                    return Some("duplicate_source_signature".to_string());
                }
                seen.insert(candidate.source_signature.clone());
            }
            Err(_) => return Some("selected_candidate_signature_lock_poisoned".to_string()),
        }
        if hot.buy_once_per_token {
            match hot.bought_mints.lock() {
                Ok(mut bought) => {
                    if bought.contains(&candidate.mint) {
                        return Some("duplicate_bought_mint".to_string());
                    }
                    bought.insert(candidate.mint.clone());
                }
                Err(_) => return Some("selected_candidate_mint_lock_poisoned".to_string()),
            }
        }
        {
            let mut active = match cfg.active_mints.lock() {
                Ok(active) => active,
                Err(_) => {
                    release_hot_reservations(hot, &candidate.source_signature, &candidate.mint, 0);
                    return Some("selected_candidate_active_mints_lock_poisoned".to_string());
                }
            };
            if active.contains(&candidate.mint) {
                release_hot_reservations(hot, &candidate.source_signature, &candidate.mint, 0);
                return Some("duplicate_active_mint".to_string());
            }
            if cfg.max_open_positions > 0 && active.len() as u64 >= cfg.max_open_positions {
                release_hot_reservations(hot, &candidate.source_signature, &candidate.mint, 0);
                return Some("selected_candidate_open_capacity_reached".to_string());
            }
            active.insert(candidate.mint.clone());
        }
        if !reserve_hot_session_lamports(hot, cfg.buy_lamports) {
            if let Ok(mut active) = cfg.active_mints.lock() {
                active.remove(&candidate.mint);
            }
            release_hot_reservations(hot, &candidate.source_signature, &candidate.mint, 0);
            return Some("selected_candidate_session_sol_cap_reached".to_string());
        }
        if !reserve_selected_candidate_session_attempt(cfg) {
            if let Ok(mut active) = cfg.active_mints.lock() {
                active.remove(&candidate.mint);
            }
            if let Ok(mut keys) = cfg.forwarded_keys.lock() {
                keys.remove(&candidate.key);
            }
            release_hot_reservations(
                hot,
                &candidate.source_signature,
                &candidate.mint,
                cfg.buy_lamports,
            );
            return Some("selected_candidate_session_attempt_capacity_reached".to_string());
        }
    }
    None
}

fn reserve_selected_candidate_session_attempt(cfg: &SelectedCandidateSniperConfig) -> bool {
    if cfg.max_session_buys == 0 {
        return true;
    }
    loop {
        let current = cfg.session_attempts.load(Ordering::SeqCst);
        if current >= cfg.max_session_buys {
            return false;
        }
        if cfg
            .session_attempts
            .compare_exchange(
                current,
                current.saturating_add(1),
                Ordering::SeqCst,
                Ordering::SeqCst,
            )
            .is_ok()
        {
            return true;
        }
    }
}

fn release_selected_candidate_reservations(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    candidate: &SelectedCandidate,
    lamports: u64,
) {
    if let Ok(mut active) = cfg.active_mints.lock() {
        active.remove(&candidate.mint);
    }
    if let Ok(mut keys) = cfg.forwarded_keys.lock() {
        keys.remove(&candidate.key);
    }
    clear_selected_candidate_pending_exact_proof(cfg, &candidate.mint);
    prune_selected_candidate_balance_watch(cfg, &candidate.mint, "release_reservations");
    release_selected_candidate_session_attempt_slot(cfg);
    release_hot_reservations(hot, &candidate.source_signature, &candidate.mint, lamports);
}

fn release_selected_candidate_session_attempt_slot(cfg: &SelectedCandidateSniperConfig) {
    if cfg.max_session_buys == 0 {
        return;
    }
    cfg.session_attempts
        .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
            Some(current.saturating_sub(1))
        })
        .ok();
}

fn release_selected_candidate_terminal_failed_attempt(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    candidate: &SelectedCandidate,
    lamports: u64,
) {
    if let Ok(mut active) = cfg.active_mints.lock() {
        active.remove(&candidate.mint);
    }
    if let Ok(mut keys) = cfg.forwarded_keys.lock() {
        keys.remove(&candidate.key);
    }
    clear_selected_candidate_pending_exact_proof(cfg, &candidate.mint);
    prune_selected_candidate_balance_watch(cfg, &candidate.mint, "terminal_failed_attempt");
    release_selected_candidate_session_attempt_slot(cfg);
    if hot.max_session_deployed_lamports.is_some() {
        hot.session_deployed_lamports
            .fetch_update(Ordering::SeqCst, Ordering::SeqCst, |current| {
                Some(current.saturating_sub(lamports))
            })
            .ok();
    }
}

fn release_selected_candidate_mint_capacity(cfg: &SelectedCandidateSniperConfig, mint: &str) {
    if let Ok(mut active) = cfg.active_mints.lock() {
        active.remove(mint);
    }
    clear_selected_candidate_pending_exact_proof(cfg, mint);
    prune_selected_candidate_balance_watch(cfg, mint, "release_mint_capacity");
}

fn mark_selected_candidate_pending_exact_proof(cfg: &SelectedCandidateSniperConfig, mint: &str) {
    let now = now_ms().min(u64::MAX as u128) as u64;
    if let Ok(mut pending) = cfg.pending_exact_proof_zero_since.lock() {
        pending.entry(mint.to_string()).or_insert(now);
    }
}

fn clear_selected_candidate_pending_exact_proof(cfg: &SelectedCandidateSniperConfig, mint: &str) {
    if let Ok(mut pending) = cfg.pending_exact_proof_zero_since.lock() {
        pending.remove(mint);
    }
}

fn prune_selected_candidate_balance_watch(
    cfg: &SelectedCandidateSniperConfig,
    mint: &str,
    reason: &'static str,
) {
    let removed_atas = cfg.balance_cache.prune_mint(mint);
    if removed_atas.is_empty() {
        return;
    }
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateBalanceCacheWatchPruned",
            "atMs": now_ms(),
            "mint": mint,
            "removedAtas": removed_atas,
            "reason": reason,
        }),
    );
}

fn prepare_selected_candidate_fast_exit_template(
    cfg: &SelectedCandidateSniperConfig,
    curve_cache: &CandidateCurveCache,
    candidate: &mut SelectedCandidate,
) -> Option<String> {
    let token2022_exit = candidate.token_program_id.as_deref() == Some(TOKEN_2022_PROGRAM_ID);
    if token2022_exit && Pubkey::from_str(&candidate.creator).is_err() {
        if let Some(cached_creator) = curve_cache
            .get_fresh(
                &candidate.bonding_curve,
                cfg.curve_cache_max_age_ms.max(2_000),
            )
            .and_then(|cached| cached.state.creator)
        {
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidatePreBuyExitCreatorCacheHit",
                    "atMs": now_ms(),
                    "mint": candidate.mint,
                    "bondingCurve": candidate.bonding_curve,
                    "creator": cached_creator,
                    "previousCreator": candidate.creator,
                }),
            );
            candidate.creator = cached_creator;
        } else {
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidatePreBuyExitCreatorCacheMiss",
                    "atMs": now_ms(),
                    "mint": candidate.mint,
                    "bondingCurve": candidate.bonding_curve,
                    "previousCreator": candidate.creator,
                    "cacheMaxAgeMs": cfg.curve_cache_max_age_ms.max(2_000),
                }),
            );
        }
    }

    match executor::build_pumpfun_exit_sell_template(
        &candidate.mint,
        &candidate.bonding_curve,
        &candidate.creator,
        candidate.token_program_id.as_deref(),
    ) {
        Ok(_) => None,
        Err(creator_err) => {
            if let Some(source_accounts) = candidate.source_ix_accounts.as_ref() {
                match executor::build_pumpfun_exit_sell_template_from_source_accounts(
                    &candidate.mint,
                    source_accounts,
                    candidate.token_program_id.as_deref(),
                ) {
                    Ok((false, _)) if token2022_exit => {
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidatePreBuyFastExitVeto",
                                "atMs": now_ms(),
                                "mint": candidate.mint,
                                "reason": "token2022_legacy_sell_template_veto",
                                "creatorTemplateError": creator_err.to_string(),
                                "sourceTemplateAccepted": false,
                                "note": "token2022 legacy-shaped source sell templates produced Custom 6074 in live calibration; require canonical v2 sell template before buy",
                            }),
                        );
                        Some("pre_buy_fast_exit_template_unavailable".to_string())
                    }
                    Ok(_) => None,
                    Err(source_err) => {
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidatePreBuyFastExitVeto",
                                "atMs": now_ms(),
                                "mint": candidate.mint,
                                "reason": "sell_template_build_failed",
                                "creatorTemplateError": creator_err.to_string(),
                                "sourceTemplateError": source_err.to_string(),
                            }),
                        );
                        Some("pre_buy_fast_exit_template_unavailable".to_string())
                    }
                }
            } else {
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidatePreBuyFastExitVeto",
                        "atMs": now_ms(),
                        "mint": candidate.mint,
                        "reason": "sell_template_build_failed",
                        "creatorTemplateError": creator_err.to_string(),
                    }),
                );
                Some("pre_buy_fast_exit_template_unavailable".to_string())
            }
        }
    }
}

fn register_selected_candidate_exit_watch(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    registry: &exit_watcher::ExitWatcherRegistry,
    action_tx: &UnboundedSender<exit_watcher::ExitAction>,
    candidate: &SelectedCandidate,
    tokens_held_raw: u64,
    safe_sell_tokens_raw: Option<u64>,
    cost_basis_lamports: u64,
    buy_confirmed: bool,
) -> bool {
    let (priority_fee_micro_lamports, jito_tip_lamports, helius_tip_lamports) =
        selected_candidate_sell_fees(hot, cost_basis_lamports);
    let token2022_exit = candidate.token_program_id.as_deref() == Some(TOKEN_2022_PROGRAM_ID);
    let mut template_creator = candidate.creator.clone();
    if token2022_exit && Pubkey::from_str(&template_creator).is_err() {
        if let Some(cached_creator) = curve_cache
            .get_fresh(
                &candidate.bonding_curve,
                cfg.curve_cache_max_age_ms.max(2_000),
            )
            .and_then(|cached| cached.state.creator)
        {
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateExitCreatorCacheHit",
                    "atMs": now_ms(),
                    "mint": candidate.mint,
                    "bondingCurve": candidate.bonding_curve,
                    "creator": cached_creator,
                    "previousCreator": candidate.creator,
                }),
            );
            template_creator = cached_creator;
        } else {
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateExitCreatorCacheMiss",
                    "atMs": now_ms(),
                    "mint": candidate.mint,
                    "bondingCurve": candidate.bonding_curve,
                    "previousCreator": candidate.creator,
                    "cacheMaxAgeMs": cfg.curve_cache_max_age_ms.max(2_000),
                }),
            );
        }
    }
    let creator_template = || {
        executor::build_pumpfun_exit_sell_template(
            &candidate.mint,
            &candidate.bonding_curve,
            &template_creator,
            candidate.token_program_id.as_deref(),
        )
    };
    let prefer_canonical_exit_template = parse_env_bool(
        "RUST_SELECTED_CANDIDATE_EXIT_PREFER_CANONICAL_TEMPLATE",
        true,
    );
    let (sell_template, sell_template_source) = if prefer_canonical_exit_template {
        match creator_template() {
            Ok(template) => (template, "creator_preferred"),
            Err(creator_err) => {
                if let Some(source_accounts) = candidate.source_ix_accounts.as_ref() {
                    match executor::build_pumpfun_exit_sell_template_from_source_accounts(
                        &candidate.mint,
                        source_accounts,
                        candidate.token_program_id.as_deref(),
                    ) {
                        Ok((false, template)) if token2022_exit => {
                            emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateExitWatchResult",
                                    "atMs": now_ms(),
                                    "ok": false,
                                    "mint": candidate.mint,
                                    "reason": "token2022_legacy_sell_template_veto",
                                    "creatorTemplateError": creator_err.to_string(),
                                    "sourceTemplateAccepted": false,
                                    "note": "token2022 legacy-shaped source sell templates produced Custom 6074 in live calibration; require canonical v2 sell template before buy",
                                }),
                            );
                            let _ = template;
                            return false;
                        }
                        Ok((false, _)) if token2022_exit => {
                            emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateExitWatchResult",
                                    "atMs": now_ms(),
                                    "ok": false,
                                    "mint": candidate.mint,
                                    "reason": "token2022_legacy_sell_template_veto",
                                    "creatorTemplateError": creator_err.to_string(),
                                }),
                            );
                            return false;
                        }
                        Ok(template) => (template, "source_accounts_fallback"),
                        Err(source_err) => {
                            emit_selected_candidate_sniper_event(
                                cfg,
                                json!({
                                    "kind": "rustSelectedCandidateExitWatchResult",
                                    "atMs": now_ms(),
                                    "ok": false,
                                    "mint": candidate.mint,
                                    "reason": "sell_template_build_failed",
                                    "creatorTemplateError": creator_err.to_string(),
                                    "sourceTemplateError": source_err.to_string(),
                                }),
                            );
                            return false;
                        }
                    }
                } else {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateExitWatchResult",
                            "atMs": now_ms(),
                            "ok": false,
                            "mint": candidate.mint,
                            "reason": "sell_template_build_failed",
                            "error": creator_err.to_string(),
                        }),
                    );
                    return false;
                }
            }
        }
    } else if let Some(source_accounts) = candidate.source_ix_accounts.as_ref() {
        match executor::build_pumpfun_exit_sell_template_from_source_accounts(
            &candidate.mint,
            source_accounts,
            candidate.token_program_id.as_deref(),
        ) {
            Ok((false, _)) if token2022_exit => match creator_template() {
                Ok(template) => (template, "creator_fallback"),
                Err(creator_err) => {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateExitWatchResult",
                            "atMs": now_ms(),
                            "ok": false,
                            "mint": candidate.mint,
                            "reason": "token2022_legacy_sell_template_veto",
                            "creatorTemplateError": creator_err.to_string(),
                            "sourceTemplateAccepted": false,
                            "note": "token2022 legacy-shaped source sell templates produced Custom 6074 in live calibration; require canonical v2 sell template before buy",
                        }),
                    );
                    return false;
                }
            },
            Ok(template) => (template, "source_accounts"),
            Err(source_err) => match creator_template() {
                Ok(template) => (template, "creator_fallback"),
                Err(creator_err) => {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateExitWatchResult",
                            "atMs": now_ms(),
                            "ok": false,
                            "mint": candidate.mint,
                            "reason": "sell_template_build_failed",
                            "sourceTemplateError": source_err.to_string(),
                            "creatorTemplateError": creator_err.to_string(),
                        }),
                    );
                    return false;
                }
            },
        }
    } else {
        match creator_template() {
            Ok(template) => (template, "creator"),
            Err(err) => {
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateExitWatchResult",
                        "atMs": now_ms(),
                        "ok": false,
                        "mint": candidate.mint,
                        "reason": "sell_template_build_failed",
                        "error": err.to_string(),
                    }),
                );
                return false;
            }
        }
    };
    let (sell_v2, accounts) = sell_template;
    let estimated_execution_fee_lamports = estimated_fee_lamports(
        priority_fee_micro_lamports,
        jito_tip_lamports.max(helius_tip_lamports),
        0,
        hot.compute_unit_limit,
    );
    let decision_execution_fee_lamports = selected_candidate_exit_decision_fee_lamports(
        estimated_execution_fee_lamports,
        cost_basis_lamports,
    );
    let exit_take_profit_pct = parse_env_f64(
        "RUST_SELECTED_CANDIDATE_EXIT_TAKE_PROFIT_PCT",
        parse_env_f64("EDGE_LIVE_TAKE_PROFIT_PCT", 8.0),
    )
    .max(0.01);
    let sell_slippage_bps = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_EXIT_SELL_SLIPPAGE_BPS",
        parse_env_u64("MIRROR_SLIPPAGE_BPS", 500),
    )
    .min(10_000);
    let normal_take_profit_capture_slippage_bps = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_EXIT_NORMAL_TP_CAPTURE_SLIPPAGE_BPS",
        parse_env_u64(
            "RUST_SELECTED_CANDIDATE_EXIT_SEND_TIME_SELL_SLIPPAGE_BPS",
            sell_slippage_bps,
        ),
    )
    .min(10_000);
    let sell_trade_fee_bps = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_EXIT_PUMPFUN_SELL_TRADE_FEE_BPS",
        PUMP_FUN_SELL_TRADE_FEE_BPS as u64,
    )
    .min(10_000);
    let tp_6003_salvage_min_realized_pnl_pct =
        parse_env_optional_f64("RUST_SELECTED_CANDIDATE_EXIT_TP_6003_SALVAGE_MIN_REALIZED_PNL_PCT")
            .or_else(|| {
                parse_env_optional_f64("RUST_SELECTED_CANDIDATE_EXIT_TP_6003_SALVAGE_MIN_PNL_PCT")
            })
            .or_else(|| {
                parse_env_optional_f64("RUST_SELECTED_CANDIDATE_EXIT_MIN_ACCEPTABLE_TP_PNL_PCT")
            });
    let status = registry.register(exit_watcher::StartPumpfunExitWatchCommand {
        id: format!("rust-selected-exit-watch-{}-{}", now_ms(), candidate.mint),
        mint: candidate.mint.clone(),
        source_wallet: candidate.source_wallet.clone(),
        bonding_curve: candidate.bonding_curve.clone(),
        entry_ts_ms: now_ms() as u64,
        tokens_held_raw,
        safe_sell_tokens_raw,
        cost_basis_lamports,
        buy_confirmed,
        exact_balance_proof: buy_confirmed && safe_sell_tokens_raw.is_none(),
        pre_proof_tp_only: !buy_confirmed
            && cfg.allow_pre_proof_sell_broadcast
            && !cfg.pre_proof_sell_require_local_landed
            && safe_sell_tokens_raw.is_some(),
        mark_telemetry_ms: parse_env_u64("RUST_SELECTED_CANDIDATE_EXIT_MARK_TELEMETRY_MS", 250)
            .max(100),
        thresholds: exit_watcher::ExitThresholds {
            stop_loss_pct: parse_env_f64(
                "RUST_SELECTED_CANDIDATE_EXIT_STOP_LOSS_PCT",
                parse_env_f64("EDGE_LIVE_STOP_LOSS_PCT", -15.0).abs(),
            )
            .abs()
            .max(0.01),
            stop_loss_grace_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EXIT_STOP_LOSS_GRACE_MS",
                parse_env_u64("EDGE_LIVE_STOP_LOSS_GRACE_MS", 0),
            ),
            take_profit_pct: exit_take_profit_pct,
            take_profit_sell_pct: parse_env_f64(
                "RUST_SELECTED_CANDIDATE_EXIT_TAKE_PROFIT_SELL_PCT",
                100.0,
            )
            .clamp(0.01, 100.0),
            early_source_window_ms: parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EXIT_EARLY_SOURCE_WINDOW_MS",
                0,
            ),
            early_source_max_pnl_pct: parse_env_f64(
                "RUST_SELECTED_CANDIDATE_EXIT_EARLY_SOURCE_MAX_PNL_PCT",
                0.0,
            ),
            source_partial_sell_pct: parse_env_f64(
                "RUST_SELECTED_CANDIDATE_EXIT_SOURCE_PARTIAL_SELL_PCT",
                100.0,
            )
            .clamp(0.01, 100.0),
            runner_drawdown_pct: parse_env_f64(
                "RUST_SELECTED_CANDIDATE_EXIT_RUNNER_DRAWDOWN_PCT",
                25.0,
            )
            .clamp(0.01, 99.99),
            max_hold_ms: parse_env_u64("RUST_SELECTED_CANDIDATE_EXIT_MAX_HOLD_MS", 0),
            sell_slippage_bps,
            sell_trade_fee_bps,
            normal_take_profit_capture_slippage_bps,
            estimated_execution_fee_lamports,
            decision_execution_fee_lamports: Some(decision_execution_fee_lamports),
            tp_6003_salvage_min_realized_pnl_pct,
            min_acceptable_take_profit_pnl_pct: None,
        },
        sell: exit_watcher::PumpfunSellTemplate {
            sell_v2,
            accounts,
            compute_unit_limit: hot.compute_unit_limit,
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
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
        },
        shadow: false,
    });
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateExitWatchResult",
            "atMs": now_ms(),
            "ok": status.ok,
            "mint": status.mint,
            "phase": status.phase,
            "actionPending": status.action_pending,
            "error": status.error,
            "sellV2": sell_v2,
            "sellTemplateSource": sell_template_source,
            "exitDecisionFeeMode": env::var("RUST_SELECTED_CANDIDATE_EXIT_DECISION_FEE_MODE")
                .unwrap_or_else(|_| "actual".to_string()),
            "estimatedExecutionFeeLamports": estimated_execution_fee_lamports,
            "decisionExecutionFeeLamports": decision_execution_fee_lamports,
            "takeProfitPct": exit_take_profit_pct,
            "sellSlippageBps": sell_slippage_bps,
            "sellTradeFeeBps": sell_trade_fee_bps,
            "normalTpCaptureSlippageBps": normal_take_profit_capture_slippage_bps,
            "tp6003SalvageMinRealizedPnlPct": tp_6003_salvage_min_realized_pnl_pct,
            "minAcceptableTakeProfitPnlPct": serde_json::Value::Null,
            "deprecatedMinAcceptableTakeProfitPnlPctIgnoredForInitialTp": true,
        }),
    );
    if buy_confirmed {
        let _ = activate_selected_candidate_exit_watch(cfg, registry, action_tx, candidate);
    }
    status.ok
}

fn activate_selected_candidate_exit_watch(
    cfg: &SelectedCandidateSniperConfig,
    registry: &exit_watcher::ExitWatcherRegistry,
    action_tx: &UnboundedSender<exit_watcher::ExitAction>,
    candidate: &SelectedCandidate,
) -> exit_watcher::ExitStatusLine {
    let (status, action) = registry.activate(
        exit_watcher::ActivatePumpfunExitWatchCommand {
            id: format!(
                "rust-selected-exit-activate-{}-{}",
                now_ms(),
                candidate.mint
            ),
            mint: candidate.mint.clone(),
        },
        now_ms() as u64,
    );
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateExitWatchActivateResult",
            "atMs": now_ms(),
            "ok": status.ok,
            "mint": status.mint,
            "phase": status.phase,
            "actionPending": status.action_pending,
            "error": status.error,
        }),
    );
    if let Some(action) = action {
        let _ = action_tx.send(action);
    }
    status
}

fn activate_selected_candidate_exit_watch_by_mint(
    cfg: &SelectedCandidateSniperConfig,
    registry: &exit_watcher::ExitWatcherRegistry,
    action_tx: &UnboundedSender<exit_watcher::ExitAction>,
    mint: &str,
    source: &str,
) -> exit_watcher::ExitStatusLine {
    let (status, action) = registry.activate(
        exit_watcher::ActivatePumpfunExitWatchCommand {
            id: format!("rust-selected-exit-activate-{}-{}", now_ms(), mint),
            mint: mint.to_string(),
        },
        now_ms() as u64,
    );
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateExitWatchActivateResult",
            "atMs": now_ms(),
            "ok": status.ok,
            "mint": status.mint,
            "phase": status.phase,
            "actionPending": status.action_pending,
            "error": status.error,
            "source": source,
        }),
    );
    if let Some(action) = action {
        let _ = action_tx.send(action);
    }
    status
}

fn maybe_activate_selected_candidate_stream_exact_balance(
    cfg: &SelectedCandidateSniperConfig,
    registry: Option<&exit_watcher::ExitWatcherRegistry>,
    action_tx: Option<&UnboundedSender<exit_watcher::ExitAction>>,
    cached: &CachedTokenAccountBalance,
    source: &str,
) {
    if !cached.exact || cached.amount_raw == 0 {
        return;
    }
    let active = cfg
        .active_mints
        .lock()
        .map(|active| active.contains(&cached.mint))
        .unwrap_or(false);
    if !active {
        return;
    }
    let Some(registry) = registry else {
        return;
    };
    let Some(action_tx) = action_tx else {
        return;
    };
    let update_status = registry.update(exit_watcher::UpdatePumpfunExitWatchCommand {
        id: format!(
            "rust-selected-stream-exact-balance-{}-{}",
            now_ms(),
            cached.mint
        ),
        mint: cached.mint.clone(),
        tokens_held_raw: cached.amount_raw,
        cost_basis_lamports: cfg.buy_lamports,
        exact_proof: true,
        proof_source: Some(cached.source.to_string()),
    });
    let activate_status = if update_status.ok {
        let status = activate_selected_candidate_exit_watch_by_mint(
            cfg,
            registry,
            action_tx,
            &cached.mint,
            source,
        );
        if status.ok {
            clear_selected_candidate_pending_exact_proof(cfg, &cached.mint);
        }
        Some(status)
    } else {
        None
    };
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateStreamingExactBalanceActivation",
            "atMs": now_ms(),
            "mint": cached.mint,
            "ata": cached.ata,
            "slot": cached.slot,
            "observedAtMs": cached.observed_at_ms,
            "amountRaw": cached.amount_raw,
            "balanceSource": cached.source,
            "source": source,
            "updateOk": update_status.ok,
            "updatePhase": update_status.phase,
            "updateActionPending": update_status.action_pending,
            "updateError": update_status.error,
            "activateOk": activate_status.as_ref().map(|status| status.ok),
            "activatePhase": activate_status.as_ref().and_then(|status| status.phase),
            "activateActionPending": activate_status
                .as_ref()
                .and_then(|status| status.action_pending),
            "activateError": activate_status.and_then(|status| status.error),
        }),
    );
}

fn selected_candidate_reject(cfg: &SelectedCandidateSniperConfig, reason: &str, extra: Value) {
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateRejected",
            "atMs": now_ms(),
            "reason": reason,
            "extra": extra,
        }),
    );
}

fn emit_selected_candidate_sniper_event(cfg: &SelectedCandidateSniperConfig, event: Value) {
    emit_json_event(cfg.audit_file.as_deref(), event);
}

fn emit_json_event(audit_file: Option<&str>, event: Value) {
    let line = event.to_string();
    println!("{line}");
    if let Some(path) = audit_file {
        if let Some(parent) = std::path::Path::new(path).parent() {
            let _ = fs::create_dir_all(parent);
        }
        if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
            let _ = writeln!(file, "{line}");
        }
    }
}

fn json_value_at<'a>(value: &'a Value, path: &[&str]) -> Option<&'a Value> {
    let mut cursor = value;
    for part in path {
        cursor = cursor.get(*part)?;
    }
    Some(cursor)
}

fn json_string_at(value: &Value, path: &[&str]) -> Option<String> {
    json_value_at(value, path).and_then(|value| match value {
        Value::String(text) if !text.trim().is_empty() => Some(text.trim().to_string()),
        Value::Number(number) => Some(number.to_string()),
        _ => None,
    })
}

fn json_bool_at(value: &Value, path: &[&str]) -> Option<bool> {
    json_value_at(value, path).and_then(Value::as_bool)
}

fn json_f64_at(value: &Value, path: &[&str]) -> Option<f64> {
    json_value_at(value, path)
        .and_then(|value| match value {
            Value::Number(number) => number.as_f64(),
            Value::String(text) => text.trim().parse::<f64>().ok(),
            _ => None,
        })
        .filter(|value| value.is_finite())
}

fn json_u64_at(value: &Value, path: &[&str]) -> Option<u64> {
    json_value_at(value, path).and_then(|value| match value {
        Value::Number(number) => number.as_u64(),
        Value::String(text) => text.trim().parse::<u64>().ok(),
        _ => None,
    })
}

fn json_string_array_at(value: &Value, path: &[&str]) -> Option<Vec<String>> {
    let values = json_value_at(value, path)?.as_array()?;
    let strings = values
        .iter()
        .filter_map(|value| value.as_str().map(str::trim))
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .collect::<Vec<_>>();
    if strings.is_empty() {
        None
    } else {
        Some(strings)
    }
}

async fn run_native_strict_shadow_feed(
    selector: NativeStrictSelectorConfig,
    selected_cfg: SelectedCandidateSniperConfig,
) {
    let Some(source) = selector
        .parity_candidate_source
        .clone()
        .or_else(|| selector.comparison_candidate_source.clone())
    else {
        return;
    };
    let mode = if selector.parity_candidate_source.is_some() {
        "parity-guard"
    } else {
        "comparison-only"
    };
    let mut offset = if selector.parity_candidate_start_at_end {
        fs::metadata(&source).map(|meta| meta.len()).unwrap_or(0)
    } else {
        0
    };
    let mut last_status_at_ms = 0u64;
    loop {
        let mut ingested = 0u64;
        let mut accepted = 0u64;
        match fs::File::open(&source) {
            Ok(mut file) => {
                let len = file.metadata().map(|meta| meta.len()).unwrap_or(0);
                if len < offset {
                    offset = 0;
                }
                if file.seek(SeekFrom::Start(offset)).is_ok() {
                    let mut reader = std::io::BufReader::new(file);
                    let mut line = String::new();
                    loop {
                        line.clear();
                        let Ok(bytes) = reader.read_line(&mut line) else {
                            break;
                        };
                        if bytes == 0 {
                            break;
                        }
                        offset = offset.saturating_add(bytes as u64);
                        ingested = ingested.saturating_add(1);
                        if let Ok(row) = serde_json::from_str::<Value>(line.trim()) {
                            if let Some((mint, candidate)) = native_strict_parity_candidate_from_row(
                                &row,
                                now_ms().min(u64::MAX as u128) as u64,
                            ) {
                                if let Ok(mut candidates) = selector.parity_candidates.lock() {
                                    let replace = candidates
                                        .get(&mint)
                                        .map(|old| candidate.observed_at_ms >= old.observed_at_ms)
                                        .unwrap_or(true);
                                    if replace {
                                        candidates.insert(mint, candidate);
                                        accepted = accepted.saturating_add(1);
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Err(err) => {
                emit_selected_candidate_sniper_event(
                    &selected_cfg,
                    json!({
                        "kind": "rustNativeStrictShadowFeedError",
                        "atMs": now_ms(),
                        "mode": mode,
                        "source": source.clone(),
                        "error": err.to_string(),
                    }),
                );
            }
        }
        let now = now_ms().min(u64::MAX as u128) as u64;
        if let Ok(mut candidates) = selector.parity_candidates.lock() {
            let max_age = selector.parity_max_age_ms.saturating_mul(30).max(60_000);
            candidates
                .retain(|_, candidate| now.saturating_sub(candidate.ingested_at_ms) <= max_age);
            if now.saturating_sub(last_status_at_ms) >= selector.status_interval_ms {
                last_status_at_ms = now;
                emit_selected_candidate_sniper_event(
                    &selected_cfg,
                    json!({
                        "kind": "rustNativeStrictShadowFeedStatus",
                        "atMs": now,
                        "mode": mode,
                        "source": source.clone(),
                        "offset": offset,
                        "cachedMints": candidates.len(),
                        "linesReadThisTick": ingested,
                        "acceptedThisTick": accepted,
                        "maxAgeMs": selector.parity_max_age_ms,
                    }),
                );
            }
        }
        sleep(Duration::from_millis(50)).await;
    }
}

fn native_strict_parity_candidate_from_row(
    row: &Value,
    ingested_at_ms: u64,
) -> Option<(String, NativeStrictParityCandidate)> {
    let event = json_string_at(row, &["event"]).or_else(|| json_string_at(row, &["kind"]));
    if event.as_deref() != Some("candidate_selected") {
        return None;
    }
    let schema = json_string_at(row, &["schemaVersion"]).unwrap_or_default();
    if schema.contains("mayhem-block")
        || json_bool_at(row, &["mayhemRetryPromoted"]).unwrap_or(false)
        || json_string_at(row, &["reason"]).is_some_and(|reason| reason.starts_with("mayhem_"))
    {
        return None;
    }
    let mint =
        json_string_at(row, &["mint"]).or_else(|| json_string_at(row, &["features", "mint"]))?;
    let token_program = json_string_at(row, &["features", "tokenProgram"])
        .or_else(|| json_string_at(row, &["features", "creationKind"]))?;
    if token_program != "token_2022" && token_program != "token2022" {
        return None;
    }
    let feature_market_cap_usd = json_f64_at(row, &["features", "marketCapUsd"])
        .or_else(|| json_f64_at(row, &["features", "entryMarketCapUsd"]))
        .or_else(|| json_f64_at(row, &["entryMarketCapUsd"]))?;
    let net_buy_sol = json_f64_at(row, &["features", "netBuySol60"])?;
    let buy_count = json_u64_at(row, &["features", "buyCount60"])?;
    let unique_wallets = json_u64_at(row, &["features", "uniqueWallets60"])?;
    let sell_pressure = json_f64_at(row, &["features", "sellPressure60"])?;
    let dominant_buy_share = json_f64_at(row, &["features", "dominantBuyShare60"])?;
    let unique_per_buy = json_f64_at(row, &["features", "uniquePerBuy60"])?;
    let buy_count_1000ms = json_u64_at(row, &["features", "buyCount1000ms"])?;
    let inter_buy_median_ms = json_u64_at(row, &["features", "interBuyMedianMs"])?;
    let first_buyer_quote_share_bps = json_u64_at(row, &["features", "firstBuyerQuoteShareBps"])?;
    let creation_age_ms = json_u64_at(row, &["features", "creationAgeMs"])?;
    if !(5_000.0..=8_000.0).contains(&feature_market_cap_usd) {
        return None;
    }
    if !(3.0..=12.0).contains(&net_buy_sol) {
        return None;
    }
    if net_buy_sol >= 5.0 && net_buy_sol < 7.0 {
        return None;
    }
    if !(3..=15).contains(&buy_count) {
        return None;
    }
    if sell_pressure > 0.02
        || dominant_buy_share > 0.4
        || unique_wallets < 3
        || unique_per_buy < 0.7
        || buy_count_1000ms < 3
        || inter_buy_median_ms > 10
        || first_buyer_quote_share_bps > 4_000
        || creation_age_ms < 500
    {
        return None;
    }
    Some((
        mint,
        NativeStrictParityCandidate {
            observed_at_ms: json_u64_at(row, &["entryObservedAtMs"])
                .or_else(|| json_u64_at(row, &["features", "observedAtMs"]))
                .unwrap_or(ingested_at_ms),
            ingested_at_ms,
            source_signature: json_string_at(row, &["sourceSignature"])
                .or_else(|| json_string_at(row, &["features", "sourceSignature"])),
            feature_market_cap_usd,
        },
    ))
}

async fn run_exit_action_loop(
    mut actions: mpsc::UnboundedReceiver<exit_watcher::ExitAction>,
    action_tx: UnboundedSender<exit_watcher::ExitAction>,
    registry: exit_watcher::ExitWatcherRegistry,
    hot: HotSniperConfig,
    selected_cfg: SelectedCandidateSniperConfig,
    curve_cache: CandidateCurveCache,
) {
    let mut exit_failure_counts: HashMap<String, u64> = HashMap::new();
    let max_finality_failures =
        parse_env_u64("RUST_SELECTED_CANDIDATE_EXIT_MAX_FINALITY_FAILURES", 3).clamp(1, 100);
    let flatten_after_failures = parse_env_bool(
        "RUST_SELECTED_CANDIDATE_EXIT_FLATTEN_AFTER_FINALITY_FAILURES",
        true,
    );
    let retry_sell_slippage_bps =
        parse_env_u64("RUST_SELECTED_CANDIDATE_EXIT_RETRY_SELL_SLIPPAGE_BPS", 0).min(10_000);
    let pre_exact_profit_sell_slippage_bps =
        parse_env_u64("RUST_SELECTED_CANDIDATE_PRE_EXACT_TP_SELL_SLIPPAGE_BPS", 0).min(10_000);
    while let Some(action) = actions.recv().await {
        let prior_failures = exit_failure_counts
            .get(&action.mint)
            .copied()
            .unwrap_or_default();
        let (mut effective_min_sol_out_lamports, retry_min_out_relaxed, exit_min_out_mode) =
            effective_exit_min_sol_out_lamports_with_pre_exact_bps(
                &action,
                prior_failures,
                retry_sell_slippage_bps,
                pre_exact_profit_sell_slippage_bps,
            );
        let mut effective_expected_sol_out_lamports = action.expected_sol_out_lamports;
        let mut send_time_sell_quote_source: Option<&'static str> = None;
        let mut send_time_sell_quote_slot: Option<u64> = None;
        let mut send_time_sell_quote_observed_at_ms: Option<u64> = None;
        let mut send_time_sell_quote_lamports: Option<u64> = None;
        let mut send_time_sell_min_out_lamports: Option<u64> = None;
        let mut send_time_sell_min_out_applied = false;
        let mut hard_stop_revalidation_source: Option<&'static str> = None;
        let mut hard_stop_revalidation_slot: Option<u64> = None;
        let mut hard_stop_revalidation_observed_at_ms: Option<u64> = None;
        let mut hard_stop_revalidated_expected_sol_out_lamports: Option<u64> = None;
        let mut hard_stop_revalidated_fee_adjusted_pnl_pct: Option<f64> = None;
        let mut hard_stop_revalidation_divergence_pp: Option<f64> = None;
        let mut hard_stop_revalidation_ms: Option<u64> = None;
        let mut hard_stop_confirmed: Option<bool> = None;
        let mut hard_stop_cancellation_reason: Option<&'static str> = None;
        if action.reason == exit_watcher::ExitReason::HardStopLoss {
            let revalidation_started = Instant::now();
            let revalidation_now = now_ms().min(u64::MAX as u128) as u64;
            let max_age_ms = parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EXIT_HARD_STOP_REVALIDATION_CURVE_CACHE_MAX_AGE_MS",
                parse_env_u64(
                    "RUST_SELECTED_CANDIDATE_EXIT_SEND_TIME_CURVE_CACHE_MAX_AGE_MS",
                    selected_cfg.curve_cache_max_age_ms,
                ),
            )
            .clamp(1, 2_000);
            let cached =
                curve_cache.get_fresh_hard_stop_authoritative(&action.bonding_curve, max_age_ms);
            let threshold_pnl_pct = action.hard_stop_threshold_pnl_pct.unwrap_or(-20.0);
            let mut authoritative_state_for_cancel = None;
            if let Some(cached) = cached {
                let refreshed_quote = quote_sell_lamports_main(
                    cached.state.virtual_token_reserves,
                    cached.state.virtual_sol_reserves,
                    action.tokens_in_raw,
                );
                let refreshed_pnl_pct = refreshed_quote.map(|quote| {
                    selected_candidate_fee_adjusted_pnl_pct(
                        quote,
                        action.cost_basis_lamports,
                        action.decision_execution_fee_lamports,
                    )
                });
                hard_stop_revalidation_source = Some(cached.source);
                hard_stop_revalidation_slot = Some(cached.slot);
                hard_stop_revalidation_observed_at_ms = Some(cached.observed_at_ms);
                hard_stop_revalidated_expected_sol_out_lamports = refreshed_quote;
                hard_stop_revalidated_fee_adjusted_pnl_pct = refreshed_pnl_pct;
                hard_stop_revalidation_divergence_pp =
                    refreshed_pnl_pct.map(|pnl| pnl - action.trigger_mark_pnl_pct);
                hard_stop_revalidation_ms = Some(
                    revalidation_started
                        .elapsed()
                        .as_millis()
                        .min(u64::MAX as u128) as u64,
                );
                authoritative_state_for_cancel = Some((
                    exit_watcher::CurveState {
                        virtual_token_reserves: cached.state.virtual_token_reserves,
                        virtual_sol_reserves: cached.state.virtual_sol_reserves,
                        complete: cached.state.complete,
                    },
                    cached.slot,
                    revalidation_now,
                    cached.source,
                ));
                if let Some(pnl) = refreshed_pnl_pct {
                    if pnl <= threshold_pnl_pct {
                        hard_stop_confirmed = Some(true);
                        effective_expected_sol_out_lamports =
                            refreshed_quote.unwrap_or(effective_expected_sol_out_lamports);
                        send_time_sell_quote_source = Some(cached.source);
                        send_time_sell_quote_slot = Some(cached.slot);
                        send_time_sell_quote_observed_at_ms = Some(cached.observed_at_ms);
                        send_time_sell_quote_lamports = refreshed_quote;
                        send_time_sell_min_out_lamports = Some(effective_min_sol_out_lamports);
                    } else {
                        hard_stop_confirmed = Some(false);
                        hard_stop_cancellation_reason = Some("authoritative_pnl_above_threshold");
                    }
                } else {
                    hard_stop_confirmed = Some(false);
                    hard_stop_cancellation_reason = Some("authoritative_quote_unavailable");
                }
            } else {
                hard_stop_confirmed = Some(false);
                hard_stop_cancellation_reason = Some("authoritative_curve_state_unavailable");
                hard_stop_revalidation_ms = Some(
                    revalidation_started
                        .elapsed()
                        .as_millis()
                        .min(u64::MAX as u128) as u64,
                );
            }

            emit_json_event(
                selected_cfg.audit_file.as_deref(),
                json!({
                    "kind": "rustExitHardStopRevalidation",
                    "atMs": now_ms(),
                    "mint": action.mint,
                    "sourceWallet": action.source_wallet,
                    "bondingCurve": action.bonding_curve,
                    "sequence": action.sequence,
                    "tokensInRaw": action.tokens_in_raw,
                    "costBasisLamports": action.cost_basis_lamports,
                    "decisionExecutionFeeLamports": action.decision_execution_fee_lamports,
                    "hardStopThresholdPnlPct": threshold_pnl_pct,
                    "hardStopTriggerSource": action.trigger_mark_source,
                    "hardStopTriggerSlot": action.trigger_mark_slot,
                    "hardStopTriggerObservedAtMs": action.trigger_mark_observed_at_ms,
                    "hardStopTriggerAgeMs": revalidation_now
                        .saturating_sub(action.trigger_mark_observed_at_ms),
                    "hardStopTriggerPnlPct": action.trigger_mark_pnl_pct,
                    "hardStopActionPnlPct": action.pnl_pct,
                    "hardStopActionExpectedSolOutLamports": action.expected_sol_out_lamports,
                    "hardStopRevalidationSource": hard_stop_revalidation_source,
                    "hardStopRevalidationSlot": hard_stop_revalidation_slot,
                    "hardStopRevalidationObservedAtMs": hard_stop_revalidation_observed_at_ms,
                    "hardStopRevalidationAgeMs": hard_stop_revalidation_observed_at_ms
                        .map(|observed| revalidation_now.saturating_sub(observed)),
                    "hardStopRevalidatedExpectedSolOutLamports":
                        hard_stop_revalidated_expected_sol_out_lamports,
                    "hardStopRevalidatedFeeAdjustedPnlPct":
                        hard_stop_revalidated_fee_adjusted_pnl_pct,
                    "hardStopRevalidationDivergencePp": hard_stop_revalidation_divergence_pp,
                    "hardStopRevalidationMs": hard_stop_revalidation_ms,
                    "hardStopConfirmed": hard_stop_confirmed,
                    "hardStopCancellationReason": hard_stop_cancellation_reason,
                    "maxAgeMs": max_age_ms,
                    "broadcastAtMs": hard_stop_confirmed
                        .filter(|confirmed| *confirmed)
                        .map(|_| now_ms()),
                }),
            );

            if hard_stop_confirmed != Some(true) {
                let status = registry.cancel_pending_hard_stop_revalidation(
                    format!(
                        "rust-exit-hard-stop-revalidation-cancel-{}-{}",
                        action.mint, action.sequence
                    ),
                    action.mint.clone(),
                    action.sequence,
                    authoritative_state_for_cancel,
                );
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitHardStopRevalidationCancel",
                        "atMs": now_ms(),
                        "mint": status.mint,
                        "sequence": action.sequence,
                        "ok": status.ok,
                        "phase": status.phase,
                        "actionPending": status.action_pending,
                        "error": status.error,
                        "reason": hard_stop_cancellation_reason
                            .unwrap_or("hard_stop_not_confirmed"),
                        "noFailedExitCooldown": true,
                    }),
                );
                continue;
            }
        }
        emit_json_event(
            selected_cfg.audit_file.as_deref(),
            json!({
                "kind": "rustExitDecision",
                "mint": action.mint,
                "sourceWallet": action.source_wallet,
                "bondingCurve": action.bonding_curve,
                "sequence": action.sequence,
                "reason": action.reason.as_str(),
                "phaseBefore": action.phase_before,
                "phaseAfterSuccess": action.phase_after_success,
                "tokensInRaw": action.tokens_in_raw,
                "expectedSolOutLamports": action.expected_sol_out_lamports,
                "minSolOutLamports": action.min_sol_out_lamports,
                "effectiveMinSolOutLamports": effective_min_sol_out_lamports,
                "costBasisLamports": action.cost_basis_lamports,
                "decisionExecutionFeeLamports": action.decision_execution_fee_lamports,
                "priorFailures": prior_failures,
                "retryMinOutRelaxed": retry_min_out_relaxed,
                "retrySellSlippageBps": retry_sell_slippage_bps,
                "preExactProfitSellSlippageBps": pre_exact_profit_sell_slippage_bps,
                "exitMinOutMode": exit_min_out_mode,
                "actionMinSolOutMode": action.min_sol_out_mode,
                "minAcceptableTakeProfitPnlPct": action.min_acceptable_take_profit_pnl_pct,
                "minAcceptableTakeProfitSolOutLamports": action.min_acceptable_take_profit_sol_out_lamports,
                "priorityFeeMicroLamports": action.sell.priority_fee_micro_lamports,
                "jitoTipLamports": action.sell.jito_tip_lamports,
                "pnlPct": action.pnl_pct,
                "grossPnlPct": action.gross_pnl_pct,
                "feeAdjustedPnlPct": action.fee_adjusted_pnl_pct,
                "triggerMarkSource": action.trigger_mark_source,
                "triggerMarkSlot": action.trigger_mark_slot,
                "triggerMarkObservedAtMs": action.trigger_mark_observed_at_ms,
                "triggerMarkPnlPct": action.trigger_mark_pnl_pct,
                "hardStopThresholdPnlPct": action.hard_stop_threshold_pnl_pct,
                "hardStopRevalidationSource": hard_stop_revalidation_source,
                "hardStopRevalidationSlot": hard_stop_revalidation_slot,
                "hardStopRevalidationObservedAtMs": hard_stop_revalidation_observed_at_ms,
                "hardStopRevalidatedExpectedSolOutLamports":
                    hard_stop_revalidated_expected_sol_out_lamports,
                "hardStopRevalidatedFeeAdjustedPnlPct":
                    hard_stop_revalidated_fee_adjusted_pnl_pct,
                "hardStopRevalidationDivergencePp": hard_stop_revalidation_divergence_pp,
                "hardStopRevalidationMs": hard_stop_revalidation_ms,
                "hardStopConfirmed": hard_stop_confirmed,
                "exactBalanceProof": action.exact_balance_proof,
                "safeSellTokensRaw": action.safe_sell_tokens_raw,
                "preExactGuaranteedSell": !action.exact_balance_proof && action.safe_sell_tokens_raw.is_some(),
                "shadow": action.shadow,
                "decidedAtMs": now_ms(),
            }),
        );

        if action.shadow {
            let (_, follow_up) = registry.ack(exit_watcher::AckPumpfunExitSellCommand {
                id: format!("rust-exit-shadow-ack-{}-{}", action.mint, action.sequence),
                mint: action.mint.clone(),
                sequence: action.sequence,
                ok: true,
                actual_sol_out_lamports: Some(action.expected_sol_out_lamports),
                error: None,
            });
            if let Some(follow_up) = follow_up {
                let _ = action_tx.send(follow_up);
            }
            continue;
        }

        let blockhash = match cached_hot_blockhash(&hot) {
            Some(blockhash) => blockhash,
            None => {
                let err = anyhow::anyhow!("fresh cached blockhash unavailable");
                let (_, follow_up) = registry.ack(exit_watcher::AckPumpfunExitSellCommand {
                    id: format!(
                        "rust-exit-blockhash-fail-{}-{}",
                        action.mint, action.sequence
                    ),
                    mint: action.mint.clone(),
                    sequence: action.sequence,
                    ok: false,
                    actual_sol_out_lamports: None,
                    error: Some(err.to_string()),
                });
                if let Some(follow_up) = follow_up {
                    let _ = action_tx.send(follow_up);
                }
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitSellFailed",
                        "mint": action.mint,
                        "sequence": action.sequence,
                        "reason": action.reason.as_str(),
                        "error": format!("cached blockhash unavailable: {err:#}"),
                    }),
                );
                continue;
            }
        };
        if parse_env_bool(
            "RUST_SELECTED_CANDIDATE_EXIT_SEND_TIME_QUOTE_REFRESH_ENABLED",
            true,
        ) && selected_candidate_exit_reason_is_profit_exit(action.reason)
        {
            let max_age_ms = parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EXIT_SEND_TIME_CURVE_CACHE_MAX_AGE_MS",
                selected_cfg.curve_cache_max_age_ms,
            )
            .clamp(1, 2_000);
            match curve_cache.get_fresh_yellowstone(&action.bonding_curve, max_age_ms) {
                Some(cached) => {
                    let refreshed_quote = quote_sell_lamports_main(
                        cached.state.virtual_token_reserves,
                        cached.state.virtual_sol_reserves,
                        action.tokens_in_raw,
                    );
                    send_time_sell_quote_source = Some(cached.source);
                    send_time_sell_quote_slot = Some(cached.slot);
                    send_time_sell_quote_observed_at_ms = Some(cached.observed_at_ms);
                    send_time_sell_quote_lamports = refreshed_quote;
                    if let Some(quote) = refreshed_quote {
                        let slippage_bps = parse_env_u64(
                            "RUST_SELECTED_CANDIDATE_EXIT_SEND_TIME_SELL_SLIPPAGE_BPS",
                            parse_env_u64("RUST_SELECTED_CANDIDATE_EXIT_SELL_SLIPPAGE_BPS", 500),
                        )
                        .min(10_000);
                        let (refreshed_min, _) =
                            selected_candidate_profit_exit_min_sol_out_lamports(
                                &action,
                                quote,
                                slippage_bps,
                            );
                        effective_expected_sol_out_lamports = quote;
                        effective_min_sol_out_lamports = refreshed_min;
                        send_time_sell_min_out_lamports = Some(refreshed_min);
                        send_time_sell_min_out_applied = true;
                    }
                    emit_json_event(
                        selected_cfg.audit_file.as_deref(),
                        json!({
                            "kind": "rustExitSendTimeQuoteRefresh",
                            "atMs": now_ms(),
                            "mint": action.mint,
                            "sequence": action.sequence,
                            "reason": action.reason.as_str(),
                            "tokensInRaw": action.tokens_in_raw,
                            "originalExpectedSolOutLamports": action.expected_sol_out_lamports,
                            "originalEffectiveMinSolOutLamports": effective_exit_min_sol_out_lamports_with_pre_exact_bps(
                                &action,
                                prior_failures,
                                retry_sell_slippage_bps,
                                pre_exact_profit_sell_slippage_bps,
                            ).0,
                            "sendTimeExpectedSolOutLamports": refreshed_quote,
                            "sendTimeMinSolOutLamports": send_time_sell_min_out_lamports,
                            "sendTimeMinOutApplied": send_time_sell_min_out_applied,
                            "sendTimeRealizableFeeAdjustedPnlPct": refreshed_quote.map(|quote| {
                                selected_candidate_fee_adjusted_pnl_pct(
                                    quote,
                                    action.cost_basis_lamports,
                                    action.decision_execution_fee_lamports,
                                )
                            }),
                            "minAcceptableTakeProfitPnlPct": action.min_acceptable_take_profit_pnl_pct,
                            "minAcceptableTakeProfitSolOutLamports": action.min_acceptable_take_profit_sol_out_lamports,
                            "normalTpCaptureSlippageBps": action.normal_take_profit_capture_slippage_bps,
                            "tp6003SalvageMinRealizedPnlPct": action.tp_6003_salvage_min_realized_pnl_pct,
                            "actionMinSolOutMode": action.min_sol_out_mode,
                            "quoteSource": cached.source,
                            "quoteSlot": cached.slot,
                            "quoteObservedAtMs": cached.observed_at_ms,
                            "quoteAgeMs": (now_ms().min(u64::MAX as u128) as u64)
                                .saturating_sub(cached.observed_at_ms),
                            "maxAgeMs": max_age_ms,
                        }),
                    );
                }
                None => emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitSendTimeQuoteRefreshMiss",
                        "atMs": now_ms(),
                        "mint": action.mint,
                        "sequence": action.sequence,
                        "reason": action.reason.as_str(),
                        "bondingCurve": action.bonding_curve,
                        "maxAgeMs": max_age_ms,
                    }),
                ),
            }
        }
        let command = executor::ExecutePumpfunSellCommand {
            id: format!("rust-exit-{}-{}", action.mint, action.sequence),
            route: if action.sell.sell_v2 {
                "pumpfun-direct-sell-v2".to_string()
            } else {
                "pumpfun-direct-sell".to_string()
            },
            mint: action.mint.clone(),
            tokens_in_raw: action.tokens_in_raw,
            expected_sol_out_lamports: effective_expected_sol_out_lamports,
            min_sol_out_lamports: effective_min_sol_out_lamports,
            sell_v2: action.sell.sell_v2,
            accounts: action
                .sell
                .accounts
                .iter()
                .map(|account| executor::SourcePumpSwapAccountMeta {
                    pubkey: account.pubkey.clone(),
                    is_signer: account.is_signer,
                    is_writable: account.is_writable,
                })
                .collect(),
            blockhash,
            compute_unit_limit: action.sell.compute_unit_limit,
            priority_fee_micro_lamports: action.sell.priority_fee_micro_lamports,
            jito_tip_lamports: action.sell.jito_tip_lamports,
            helius_tip_lamports: action.sell.helius_tip_lamports,
            use_jito_direct: action.sell.use_jito_direct,
            use_jito_fallback: action.sell.use_jito_fallback,
            sender_rpc_fallback_enabled: action.sell.sender_rpc_fallback_enabled,
            execution_multi_send_enabled: action.sell.execution_multi_send_enabled,
            jito_send_mode: action.sell.jito_send_mode.clone(),
            jito_transaction_urls: action.sell.jito_transaction_urls.clone(),
            jito_bundle_urls: action.sell.jito_bundle_urls.clone(),
            sender_endpoint_url: action.sell.sender_endpoint_url.clone(),
            rpc_fallback_url: action.sell.rpc_fallback_url.clone(),
            jito_auth_uuid: action.sell.jito_auth_uuid.clone(),
        };
        let started = Instant::now();
        match executor::execute_pumpfun_sell_command(command, started).await {
            Ok(response) if response.ok => {
                let signature = response.signature.unwrap_or_default();
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitSellBroadcast",
                        "atMs": now_ms(),
                        "mint": action.mint,
                        "sequence": action.sequence,
                        "reason": action.reason.as_str(),
                        "signature": signature,
                        "route": response.route,
                        "senderPath": response.sender_path,
                        "tokensInRaw": action.tokens_in_raw,
                        "expectedSolOutLamports": action.expected_sol_out_lamports,
                        "effectiveExpectedSolOutLamports": effective_expected_sol_out_lamports,
                        "minSolOutLamports": action.min_sol_out_lamports,
                        "effectiveMinSolOutLamports": effective_min_sol_out_lamports,
                        "costBasisLamports": action.cost_basis_lamports,
                        "decisionExecutionFeeLamports": action.decision_execution_fee_lamports,
                        "sendTimeSellQuoteSource": send_time_sell_quote_source,
                        "sendTimeSellQuoteSlot": send_time_sell_quote_slot,
                        "sendTimeSellQuoteObservedAtMs": send_time_sell_quote_observed_at_ms,
                        "sendTimeSellQuoteLamports": send_time_sell_quote_lamports,
                        "sendTimeSellMinOutLamports": send_time_sell_min_out_lamports,
                        "sendTimeSellMinOutApplied": send_time_sell_min_out_applied,
                        "sendTimeRealizableFeeAdjustedPnlPct": send_time_sell_quote_lamports.map(|quote| {
                            selected_candidate_fee_adjusted_pnl_pct(
                                quote,
                                action.cost_basis_lamports,
                                action.decision_execution_fee_lamports,
                            )
                        }),
                        "actionMinSolOutMode": action.min_sol_out_mode,
                        "minAcceptableTakeProfitPnlPct": action.min_acceptable_take_profit_pnl_pct,
                        "minAcceptableTakeProfitSolOutLamports": action.min_acceptable_take_profit_sol_out_lamports,
                        "normalTpCaptureSlippageBps": action.normal_take_profit_capture_slippage_bps,
                        "tp6003SalvageMinRealizedPnlPct": action.tp_6003_salvage_min_realized_pnl_pct,
                        "priorFailures": prior_failures,
                        "retryMinOutRelaxed": retry_min_out_relaxed,
                        "retrySellSlippageBps": retry_sell_slippage_bps,
                        "preExactProfitSellSlippageBps": pre_exact_profit_sell_slippage_bps,
                        "exitMinOutMode": exit_min_out_mode,
                        "pnlPct": action.pnl_pct,
                        "grossPnlPct": action.gross_pnl_pct,
                        "feeAdjustedPnlPct": action.fee_adjusted_pnl_pct,
                        "triggerMarkSource": action.trigger_mark_source,
                        "triggerMarkSlot": action.trigger_mark_slot,
                        "triggerMarkObservedAtMs": action.trigger_mark_observed_at_ms,
                        "triggerMarkPnlPct": action.trigger_mark_pnl_pct,
                        "hardStopThresholdPnlPct": action.hard_stop_threshold_pnl_pct,
                        "hardStopRevalidationSource": hard_stop_revalidation_source,
                        "hardStopRevalidationSlot": hard_stop_revalidation_slot,
                        "hardStopRevalidationObservedAtMs": hard_stop_revalidation_observed_at_ms,
                        "hardStopRevalidatedExpectedSolOutLamports":
                            hard_stop_revalidated_expected_sol_out_lamports,
                        "hardStopRevalidatedFeeAdjustedPnlPct":
                            hard_stop_revalidated_fee_adjusted_pnl_pct,
                        "hardStopRevalidationDivergencePp": hard_stop_revalidation_divergence_pp,
                        "hardStopRevalidationMs": hard_stop_revalidation_ms,
                        "hardStopConfirmed": hard_stop_confirmed,
                        "exactBalanceProof": action.exact_balance_proof,
                        "safeSellTokensRaw": action.safe_sell_tokens_raw,
                        "preExactGuaranteedSell": !action.exact_balance_proof && action.safe_sell_tokens_raw.is_some(),
                        "elapsedMs": response.elapsed_ms,
                        "buildMs": response.build_ms,
                        "broadcastMs": response.broadcast_ms,
                        "txBytes": response.tx_bytes,
                    }),
                );
                let finality = confirm_signature_status(
                    &action.sell.rpc_fallback_url,
                    &signature,
                    parse_env_u64("RUST_SELECTED_CANDIDATE_SELL_STATUS_TIMEOUT_MS", 2_000),
                    parse_env_u64("RUST_SELECTED_CANDIDATE_SELL_STATUS_POLL_MS", 100),
                )
                .await;
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitSellFinality",
                        "atMs": now_ms(),
                        "mint": action.mint,
                        "sequence": action.sequence,
                        "signature": signature,
                        "status": finality.status,
                        "confirmationStatus": finality.confirmation_status,
                        "slot": finality.slot,
                        "error": finality.error,
                    }),
                );
                if finality.ok != Some(true) {
                    handle_failed_exit(
                        &registry,
                        &action_tx,
                        &action,
                        &selected_cfg,
                        &hot,
                        &curve_cache,
                        &mut exit_failure_counts,
                        max_finality_failures,
                        flatten_after_failures,
                        format!(
                            "sell not finalized ok: status={} error={}",
                            finality.status,
                            finality.error.unwrap_or_else(|| "unknown".to_string())
                        ),
                    )
                    .await;
                    continue;
                }
                exit_failure_counts.remove(&action.mint);
                let (status, follow_up) = registry.ack(exit_watcher::AckPumpfunExitSellCommand {
                    id: format!("rust-exit-sell-ack-{}-{}", action.mint, action.sequence),
                    mint: action.mint.clone(),
                    sequence: action.sequence,
                    ok: true,
                    actual_sol_out_lamports: Some(action.expected_sol_out_lamports),
                    error: None,
                });
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitSellAck",
                        "atMs": now_ms(),
                        "ok": status.ok,
                        "mint": status.mint,
                        "phase": status.phase,
                        "actionPending": status.action_pending,
                        "error": status.error,
                    }),
                );
                if matches!(
                    status.phase,
                    Some(exit_watcher::ExitPhase::Closed | exit_watcher::ExitPhase::Graduated)
                ) {
                    release_selected_candidate_mint_capacity(&selected_cfg, &action.mint);
                }
                if let Some(follow_up) = follow_up {
                    let _ = action_tx.send(follow_up);
                }
            }
            Ok(response) => {
                handle_failed_exit(
                    &registry,
                    &action_tx,
                    &action,
                    &selected_cfg,
                    &hot,
                    &curve_cache,
                    &mut exit_failure_counts,
                    max_finality_failures,
                    flatten_after_failures,
                    response
                        .error
                        .unwrap_or_else(|| "Rust executor rejected exit".to_string()),
                )
                .await;
            }
            Err(err) => {
                handle_failed_exit(
                    &registry,
                    &action_tx,
                    &action,
                    &selected_cfg,
                    &hot,
                    &curve_cache,
                    &mut exit_failure_counts,
                    max_finality_failures,
                    flatten_after_failures,
                    err.to_string(),
                )
                .await;
            }
        }
    }
}

fn effective_exit_min_sol_out_lamports(
    action: &exit_watcher::ExitAction,
    prior_failures: u64,
    retry_sell_slippage_bps: u64,
) -> (u64, bool, &'static str) {
    effective_exit_min_sol_out_lamports_with_pre_exact_bps(
        action,
        prior_failures,
        retry_sell_slippage_bps,
        0,
    )
}

fn selected_candidate_profit_exit_min_sol_out_lamports(
    action: &exit_watcher::ExitAction,
    quote_lamports: u64,
    slippage_bps: u64,
) -> (u64, &'static str) {
    if let Some(floor) = action.min_acceptable_take_profit_sol_out_lamports {
        return (floor.max(1), "tp_6003_salvage_min_realized_pnl");
    }
    (
        quote_lamports.saturating_mul(10_000_u64.saturating_sub(slippage_bps.min(10_000))) / 10_000,
        "normal_take_profit_capture",
    )
}

fn selected_candidate_fee_adjusted_pnl_pct(
    expected_sol_out_lamports: u64,
    cost_basis_lamports: u64,
    decision_execution_fee_lamports: u64,
) -> f64 {
    if cost_basis_lamports == 0 {
        return 0.0;
    }
    let net = expected_sol_out_lamports.saturating_sub(decision_execution_fee_lamports);
    ((net as f64 / cost_basis_lamports as f64) - 1.0) * 100.0
}

fn selected_candidate_pre_exact_safe_sell_tokens_raw(
    submitted_min_tokens_out_raw: u64,
    quoted_tokens_out_raw: u64,
    guaranteed_exit_bps: u64,
    pre_exact_cap_bps: u64,
) -> u64 {
    let guaranteed_exit_raw = ((submitted_min_tokens_out_raw as u128
        * guaranteed_exit_bps.min(10_000) as u128)
        / 10_000_u128)
        .max(1);
    let pre_exact_cap_raw = ((submitted_min_tokens_out_raw as u128
        * pre_exact_cap_bps.min(10_000) as u128)
        / 10_000_u128)
        .max(1);
    guaranteed_exit_raw
        .min(pre_exact_cap_raw)
        .min(quoted_tokens_out_raw as u128)
        .max(1) as u64
}

fn effective_exit_min_sol_out_lamports_with_pre_exact_bps(
    action: &exit_watcher::ExitAction,
    prior_failures: u64,
    retry_sell_slippage_bps: u64,
    pre_exact_profit_sell_slippage_bps: u64,
) -> (u64, bool, &'static str) {
    if !selected_candidate_exit_reason_is_profit_exit(action.reason) {
        return (action.min_sol_out_lamports, false, "protected");
    }
    if let Some(floor) = action.min_acceptable_take_profit_sol_out_lamports {
        return (
            floor.max(1),
            floor < action.min_sol_out_lamports,
            "tp_6003_salvage_min_realized_pnl",
        );
    }
    if prior_failures == 0
        && pre_exact_profit_sell_slippage_bps > 0
        && !action.exact_balance_proof
        && action.safe_sell_tokens_raw.is_some()
    {
        let pre_exact_min = action.expected_sol_out_lamports.saturating_mul(
            10_000_u64.saturating_sub(pre_exact_profit_sell_slippage_bps.min(10_000)),
        ) / 10_000;
        let pre_exact_min = pre_exact_min.max(1);
        if pre_exact_min < action.min_sol_out_lamports {
            return (pre_exact_min, true, "pre_exact_profit_landing");
        }
    }
    if prior_failures == 0 || retry_sell_slippage_bps == 0 {
        return (action.min_sol_out_lamports, false, "protected");
    }
    let retry_min = action
        .expected_sol_out_lamports
        .saturating_mul(10_000_u64.saturating_sub(retry_sell_slippage_bps.min(10_000)))
        / 10_000;
    let retry_min = retry_min.max(1);
    if retry_min < action.min_sol_out_lamports {
        (retry_min, true, "retry_relaxed")
    } else {
        (action.min_sol_out_lamports, false, "protected")
    }
}

fn selected_candidate_exit_reason_is_profit_exit(reason: exit_watcher::ExitReason) -> bool {
    matches!(
        reason,
        exit_watcher::ExitReason::TakeProfit
            | exit_watcher::ExitReason::SourcePartial
            | exit_watcher::ExitReason::RunnerDrawdown
            | exit_watcher::ExitReason::RunnerSourceSell
    )
}

async fn run_selected_candidate_wallet_reconciler(
    cfg: SelectedCandidateSniperConfig,
    hot: HotSniperConfig,
    registry: Option<exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<UnboundedSender<exit_watcher::ExitAction>>,
) {
    loop {
        if let Err(err) = reconcile_selected_candidate_wallet_positions(
            &cfg,
            &hot,
            registry.as_ref(),
            exit_actions.as_ref(),
        )
        .await
        {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateWalletReconcileError",
                    "atMs": now_ms(),
                    "error": err.to_string(),
                }),
            );
        }
        sleep(Duration::from_millis(cfg.wallet_reconcile_interval_ms)).await;
    }
}

async fn reconcile_selected_candidate_wallet_positions(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    registry: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let positions =
        fetch_selected_candidate_wallet_nonzero_token_positions(&hot.rpc_fallback_url).await?;
    let active_mints = selected_candidate_active_mints(cfg, registry);
    let tracked_mints = selected_candidate_tracked_mints(registry);
    let now = now_ms().min(u64::MAX as u128) as u64;
    let current_mints = positions
        .iter()
        .map(|position| position.mint.clone())
        .collect::<HashSet<_>>();
    if let Ok(mut seen) = cfg.wallet_reconcile_seen.lock() {
        seen.retain(|mint, _| current_mints.contains(mint) || tracked_mints.contains(mint));
    }

    for position in positions
        .iter()
        .filter(|position| tracked_mints.contains(&position.mint))
    {
        if let Ok(mut seen) = cfg.wallet_reconcile_seen.lock() {
            seen.insert(position.mint.clone(), now);
        }
        if let Some(registry) = registry {
            let (status, action) = registry.reconcile_wallet_balance(
                format!("wallet-reconcile-balance-{}-{}", now_ms(), position.mint),
                position.mint.clone(),
                position.amount_raw,
                cfg.buy_lamports.max(1),
                now,
            );
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateWalletManagedBalanceUpdate",
                    "atMs": now_ms(),
                    "mint": position.mint,
                    "tokenProgramId": position.token_program_id,
                    "amountRaw": position.amount_raw,
                    "uiAmount": position.ui_amount,
                    "ok": status.ok,
                    "phase": status.phase,
                    "actionPending": status.action_pending,
                    "error": status.error,
                }),
            );
            if let Some(action) = action {
                if let Some(tx) = exit_actions {
                    let _ = tx.send(action);
                }
            } else if cfg.wallet_reconcile_auto_flatten_enabled
                && registry.should_flatten_wallet_residual(&position.mint)
            {
                let flatten_position = position.clone();
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateWalletManagedResidualFlattenStart",
                        "atMs": now_ms(),
                        "mint": flatten_position.mint,
                        "tokenProgramId": flatten_position.token_program_id,
                        "amountRaw": flatten_position.amount_raw,
                        "uiAmount": flatten_position.ui_amount,
                        "reason": "managed_position_should_be_flat",
                    }),
                );
                match flatten_selected_candidate_wallet_position(cfg, hot, &flatten_position).await
                {
                    Ok(()) => {
                        let close_status = registry.reconcile_wallet_flat(
                            format!(
                                "wallet-managed-residual-flat-{}-{}",
                                now_ms(),
                                flatten_position.mint
                            ),
                            flatten_position.mint.clone(),
                        );
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidateWalletManagedResidualFlattenResult",
                                "atMs": now_ms(),
                                "mint": flatten_position.mint,
                                "ok": true,
                                "closeOk": close_status.ok,
                                "closePhase": close_status.phase,
                                "closeError": close_status.error,
                            }),
                        );
                    }
                    Err(err) => {
                        emit_selected_candidate_sniper_event(
                            cfg,
                            json!({
                                "kind": "rustSelectedCandidateWalletManagedResidualFlattenResult",
                                "atMs": now_ms(),
                                "mint": flatten_position.mint,
                                "ok": false,
                                "error": err.to_string(),
                            }),
                        );
                    }
                }
            }
        }
    }

    if let Some(registry) = registry {
        let tracked_flat = tracked_mints
            .iter()
            .filter(|mint| !current_mints.contains(*mint))
            .cloned()
            .collect::<Vec<_>>();
        for mint in tracked_flat {
            let was_seen = cfg
                .wallet_reconcile_seen
                .lock()
                .map(|seen| seen.contains_key(&mint))
                .unwrap_or(false);
            if !was_seen {
                continue;
            }
            let status = registry.reconcile_wallet_flat(
                format!("wallet-reconcile-flat-{}-{}", now_ms(), mint),
                mint.clone(),
            );
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateWalletTrackedFlat",
                    "atMs": now_ms(),
                    "mint": mint,
                    "ok": status.ok,
                    "phase": status.phase,
                    "actionPending": status.action_pending,
                    "error": status.error,
                    "reason": "wallet_zero_balance_after_seen",
                }),
            );
            if status.ok {
                if let Ok(mut seen) = cfg.wallet_reconcile_seen.lock() {
                    seen.remove(&mint);
                }
                release_selected_candidate_mint_capacity(cfg, &mint);
            }
        }
    }

    let pending_zero_release_ms = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_PENDING_EXACT_PROOF_ZERO_RELEASE_MS",
        cfg.wallet_reconcile_interval_ms
            .saturating_mul(3)
            .max(5_000),
    )
    .clamp(2_000, 300_000);
    let pending_zero = cfg
        .pending_exact_proof_zero_since
        .lock()
        .map(|pending| pending.clone())
        .unwrap_or_default();
    for (mint, first_pending_ms) in pending_zero {
        if current_mints.contains(&mint) {
            clear_selected_candidate_pending_exact_proof(cfg, &mint);
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidatePendingExactProofResolved",
                    "atMs": now_ms(),
                    "mint": mint,
                    "reason": "wallet_balance_found",
                }),
            );
            continue;
        }
        let age_ms = now.saturating_sub(first_pending_ms);
        if age_ms < pending_zero_release_ms {
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidatePendingExactProofStillZero",
                    "atMs": now_ms(),
                    "mint": mint,
                    "firstPendingMs": first_pending_ms,
                    "ageMs": age_ms,
                    "releaseAfterMs": pending_zero_release_ms,
                    "tracked": tracked_mints.contains(&mint),
                    "active": active_mints.contains(&mint),
                }),
            );
            continue;
        }
        let mut cancel_ok = None;
        let mut cancel_phase = None;
        let mut cancel_error = None;
        if let Some(registry) = registry {
            let status = registry.cancel(exit_watcher::CancelPumpfunExitWatchCommand {
                id: format!("wallet-reconcile-zero-release-{}-{}", now_ms(), mint),
                mint: mint.clone(),
                reason: Some(
                    "pending exact proof expired with repeated zero wallet balance".to_string(),
                ),
            });
            cancel_ok = Some(status.ok);
            cancel_phase = status.phase;
            cancel_error = status.error;
        }
        release_selected_candidate_mint_capacity(cfg, &mint);
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidatePendingExactProofZeroReleased",
                "atMs": now_ms(),
                "mint": mint,
                "firstPendingMs": first_pending_ms,
                "ageMs": age_ms,
                "releaseAfterMs": pending_zero_release_ms,
                "cancelOk": cancel_ok,
                "cancelPhase": cancel_phase,
                "cancelError": cancel_error,
                "reason": "wallet_zero_balance_after_pending_exact_proof_timeout",
            }),
        );
    }

    let unmanaged = positions
        .iter()
        .filter(|position| {
            !active_mints.contains(&position.mint) && !tracked_mints.contains(&position.mint)
        })
        .cloned()
        .collect::<Vec<_>>();
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateWalletReconcile",
            "atMs": now_ms(),
            "positions": positions.len(),
            "managed": positions.len().saturating_sub(unmanaged.len()),
            "unmanaged": unmanaged.len(),
            "autoFlattenEnabled": cfg.wallet_reconcile_auto_flatten_enabled,
        }),
    );

    for position in unmanaged {
        let first_seen_ms = {
            let mut seen = cfg
                .wallet_reconcile_seen
                .lock()
                .map_err(|_| anyhow::anyhow!("wallet reconcile state lock poisoned"))?;
            *seen.entry(position.mint.clone()).or_insert(now)
        };
        let age_ms = now.saturating_sub(first_seen_ms);
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateWalletUnmanagedPosition",
                "atMs": now_ms(),
                "mint": position.mint,
                "tokenProgramId": position.token_program_id,
                "amountRaw": position.amount_raw,
                "uiAmount": position.ui_amount,
                "firstSeenMs": first_seen_ms,
                "ageMs": age_ms,
                "autoFlattenEnabled": cfg.wallet_reconcile_auto_flatten_enabled,
                "unmanagedGraceMs": cfg.wallet_reconcile_unmanaged_grace_ms,
            }),
        );
        if cfg.wallet_reconcile_auto_flatten_enabled
            && age_ms >= cfg.wallet_reconcile_unmanaged_grace_ms
        {
            match flatten_selected_candidate_wallet_position(cfg, hot, &position).await {
                Ok(()) => {
                    if let Ok(mut seen) = cfg.wallet_reconcile_seen.lock() {
                        seen.remove(&position.mint);
                    }
                }
                Err(err) => {
                    emit_selected_candidate_sniper_event(
                        cfg,
                        json!({
                            "kind": "rustSelectedCandidateWalletFlattenError",
                            "atMs": now_ms(),
                            "mint": position.mint,
                            "tokenProgramId": position.token_program_id,
                            "amountRaw": position.amount_raw,
                            "error": err.to_string(),
                        }),
                    );
                }
            }
        }
    }
    Ok(())
}

fn selected_candidate_active_mints(
    cfg: &SelectedCandidateSniperConfig,
    registry: Option<&exit_watcher::ExitWatcherRegistry>,
) -> HashSet<String> {
    let mut active = cfg
        .active_mints
        .lock()
        .map(|mints| mints.iter().cloned().collect::<HashSet<_>>())
        .unwrap_or_default();
    if let Some(registry) = registry {
        active.extend(registry.active_mints());
    }
    active
}

fn selected_candidate_tracked_mints(
    registry: Option<&exit_watcher::ExitWatcherRegistry>,
) -> HashSet<String> {
    registry
        .map(|registry| registry.tracked_mints().into_iter().collect::<HashSet<_>>())
        .unwrap_or_default()
}

fn selected_candidate_actual_fill_degradation_pct(
    expected_tokens_out_raw: u64,
    actual_tokens_out_raw: u64,
) -> Option<f64> {
    if expected_tokens_out_raw == 0 || actual_tokens_out_raw == 0 {
        return None;
    }
    let degradation =
        (1.0 - (actual_tokens_out_raw as f64 / expected_tokens_out_raw as f64)) * 100.0;
    degradation.is_finite().then_some(degradation)
}

fn selected_candidate_implied_actual_entry_mc_usd(
    expected_entry_mc_usd: Option<f64>,
    expected_tokens_out_raw: u64,
    actual_tokens_out_raw: u64,
) -> Option<f64> {
    let expected_entry_mc_usd =
        expected_entry_mc_usd.filter(|value| value.is_finite() && *value > 0.0)?;
    if expected_tokens_out_raw == 0 || actual_tokens_out_raw == 0 {
        return None;
    }
    let implied =
        expected_entry_mc_usd * (expected_tokens_out_raw as f64 / actual_tokens_out_raw as f64);
    implied.is_finite().then_some(implied)
}

fn selected_candidate_protected_min_tokens_out_raw(
    cfg: &SelectedCandidateSniperConfig,
    candidate: &SelectedCandidate,
    effective_max_entry_drift_bps: u64,
) -> Option<u64> {
    if candidate.entry_market_cap_usd <= 0.0 || !candidate.entry_market_cap_usd.is_finite() {
        return None;
    }
    if candidate.shadow_tokens_out_raw == 0 || candidate.shadow_sol_in_lamports == 0 {
        return None;
    }
    let mut allowed_drift_bps = effective_max_entry_drift_bps;
    if let Some(max_mc) = cfg
        .max_entry_market_cap_usd
        .filter(|value| value.is_finite() && *value > 0.0)
    {
        let cap_bps = ((max_mc / candidate.entry_market_cap_usd) - 1.0) * 10_000.0;
        if !cap_bps.is_finite() || cap_bps < 0.0 {
            return None;
        }
        allowed_drift_bps = allowed_drift_bps.min(cap_bps.floor() as u64);
    }
    let expected_shadow_tokens = ((candidate.shadow_tokens_out_raw as u128)
        * (cfg.buy_lamports as u128))
        / (candidate.shadow_sol_in_lamports as u128);
    if expected_shadow_tokens == 0 {
        return None;
    }
    let protected_min_tokens =
        (expected_shadow_tokens * 10_000u128) / (10_000u128 + allowed_drift_bps as u128);
    u64::try_from(protected_min_tokens.max(1)).ok()
}

fn selected_candidate_min_out_plan(
    cfg: &SelectedCandidateSniperConfig,
    candidate: &SelectedCandidate,
    effective_max_entry_drift_bps: u64,
    effective_landing_buffer_bps: u64,
) -> Value {
    let expected_shadow_tokens = ((candidate.shadow_tokens_out_raw as u128)
        * (cfg.buy_lamports as u128))
        / (candidate.shadow_sol_in_lamports.max(1) as u128);
    let mut allowed_drift_bps = effective_max_entry_drift_bps;
    if let Some(max_mc) = cfg
        .max_entry_market_cap_usd
        .filter(|value| value.is_finite() && *value > 0.0)
    {
        let cap_bps = ((max_mc / candidate.entry_market_cap_usd) - 1.0) * 10_000.0;
        if cap_bps.is_finite() && cap_bps >= 0.0 {
            allowed_drift_bps = allowed_drift_bps.min(cap_bps.floor() as u64);
        }
    }
    let protected_min_tokens_out_raw =
        (expected_shadow_tokens * 10_000u128) / (10_000u128 + allowed_drift_bps as u128);
    let quoted_tokens_out_raw = quote_buy_tokens_out_raw_main(
        candidate.virtual_sol_reserves as u128,
        candidate.virtual_token_reserves as u128,
        cfg.buy_lamports as u128,
    )
    .ok();
    let calibrated_quote_tokens_out_raw =
        quoted_tokens_out_raw.map(|quote| apply_bps_haircut_u128(quote, cfg.fee_quote_haircut_bps));
    let protected_floor_tokens_out_raw = apply_bps_haircut_u128(
        protected_min_tokens_out_raw,
        cfg.max_min_out_below_protected_bps,
    );
    let buffered_min_tokens_out_raw = calibrated_quote_tokens_out_raw
        .map(|quote| apply_bps_haircut_u128(quote, effective_landing_buffer_bps));
    let allow_below_protected = parse_env_bool(
        "RUST_SELECTED_CANDIDATE_ALLOW_LANDING_BUFFER_BELOW_PROTECTED_MIN",
        false,
    );
    let fill_first = selected_candidate_fill_first_buy_policy(cfg);
    let submitted_min_tokens_out_raw = if fill_first {
        1
    } else if allow_below_protected {
        buffered_min_tokens_out_raw
            .unwrap_or(protected_min_tokens_out_raw)
            .max(protected_floor_tokens_out_raw)
    } else {
        protected_min_tokens_out_raw
    };
    json!({
        "shadowTokensOutRaw": candidate.shadow_tokens_out_raw,
        "shadowSolInLamports": candidate.shadow_sol_in_lamports,
        "expectedShadowTokensOutRaw": u64::try_from(expected_shadow_tokens).ok(),
        "quotedTokensOutRaw": quoted_tokens_out_raw.and_then(|value| u64::try_from(value).ok()),
        "calibratedQuoteTokensOutRaw": calibrated_quote_tokens_out_raw.and_then(|value| u64::try_from(value).ok()),
        "protectedMinTokensOutRaw": u64::try_from(protected_min_tokens_out_raw).ok(),
        "protectedFloorTokensOutRaw": u64::try_from(protected_floor_tokens_out_raw).ok(),
        "bufferedMinTokensOutRaw": buffered_min_tokens_out_raw.and_then(|value| u64::try_from(value).ok()),
        "submittedMinTokensOutRaw": u64::try_from(submitted_min_tokens_out_raw.max(1)).ok(),
        "buyOutputPolicy": cfg.buy_output_policy.clone(),
        "fillFirst": fill_first,
        "fillFirstMinOutRaw": if fill_first { Some(1_u64) } else { None },
        "economicEntryProtection": "send_time_mc_drift_and_protected_quote_floor",
        "allowedDriftBps": allowed_drift_bps,
        "effectiveMaxEntryDriftBps": effective_max_entry_drift_bps,
        "effectiveLandingBufferBps": effective_landing_buffer_bps,
        "feeQuoteHaircutBps": cfg.fee_quote_haircut_bps,
        "maxMinOutBelowProtectedBps": cfg.max_min_out_below_protected_bps,
        "allowLandingBufferBelowProtectedMin": allow_below_protected,
        "maxEntryMarketCapUsd": cfg.max_entry_market_cap_usd,
        "pumpfunTotalFeeBps": parse_env_u64(
            "RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS",
            PUMP_FUN_TRADE_FEE_BPS as u64,
        )
        .min(2_000),
    })
}

struct SelectedCandidateSourceIxDataInfo {
    len: Option<usize>,
    discriminator_hex: Option<String>,
    discriminator_kind: &'static str,
}

fn selected_candidate_source_ix_data_info(
    source_ix_data_base64: Option<&str>,
) -> SelectedCandidateSourceIxDataInfo {
    let Some(encoded) = source_ix_data_base64.filter(|value| !value.is_empty()) else {
        return SelectedCandidateSourceIxDataInfo {
            len: None,
            discriminator_hex: None,
            discriminator_kind: "missing",
        };
    };
    let Ok(data) = BASE64_STANDARD.decode(encoded) else {
        return SelectedCandidateSourceIxDataInfo {
            len: None,
            discriminator_hex: None,
            discriminator_kind: "invalid_base64",
        };
    };
    let discriminator_hex = (data.len() >= 8).then(|| discriminator_hex8(&data[..8]));
    let discriminator_kind = if starts_with(&data, &BUY_DISCRIMINATOR) {
        "pumpfun_buy_legacy"
    } else if starts_with(&data, &BUY_EXACT_SOL_IN_DISCRIMINATOR) {
        "pumpfun_buy_exact_sol_in_legacy"
    } else if starts_with(&data, &BUY_V2_DISCRIMINATOR) {
        "pumpfun_buy_v2"
    } else if starts_with(&data, &BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR) {
        "pumpfun_buy_exact_quote_in_v2"
    } else if starts_with(&data, &PUMPSWAP_BUY_EXACT_QUOTE_IN_DISCRIMINATOR) {
        "pumpswap_buy_exact_quote_in"
    } else {
        "unknown"
    };
    SelectedCandidateSourceIxDataInfo {
        len: Some(data.len()),
        discriminator_hex,
        discriminator_kind,
    }
}

fn discriminator_hex8(bytes: &[u8]) -> String {
    bytes
        .iter()
        .take(8)
        .map(|byte| format!("{byte:02x}"))
        .collect::<Vec<_>>()
        .join("")
}

fn apply_bps_haircut_u64(value: u64, bps: u64) -> u64 {
    u64::try_from(apply_bps_haircut_u128(value as u128, bps)).unwrap_or(u64::MAX)
}

fn apply_bps_haircut_u128(value: u128, bps: u64) -> u128 {
    value.saturating_mul(10_000u128.saturating_sub(bps.min(10_000) as u128)) / 10_000u128
}

fn ceil_div_u128(numerator: u128, denominator: u128) -> Result<u128> {
    if denominator == 0 {
        bail!("ceil_div denominator is zero");
    }
    Ok(numerator
        .checked_add(denominator.saturating_sub(1))
        .context("ceil_div numerator overflow")?
        / denominator)
}

fn fill_drift_bps(current_tokens_out_raw: u64, quoted_tokens_out_raw: u64) -> Option<i64> {
    if quoted_tokens_out_raw == 0 {
        return None;
    }
    let drift = ((quoted_tokens_out_raw as i128 - current_tokens_out_raw as i128) * 10_000i128)
        / quoted_tokens_out_raw as i128;
    i64::try_from(drift).ok()
}

fn fill_ratio_pct(numerator_tokens_raw: u64, denominator_tokens_raw: u64) -> Option<f64> {
    if denominator_tokens_raw == 0 {
        return None;
    }
    Some((numerator_tokens_raw as f64 / denominator_tokens_raw as f64) * 100.0)
}

async fn emit_selected_candidate_6042_counterfactual(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    candidate: &SelectedCandidate,
    min_out_plan: &Value,
    signature: &str,
    quoted_tokens_out_raw: u64,
    sol_in_lamports: u64,
    finality_slot: Option<u64>,
) {
    let started_at_ms = now_ms();
    let cache_max_age_ms = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_6042_COUNTERFACTUAL_CACHE_MAX_AGE_MS",
        2_000,
    )
    .clamp(25, 10_000);
    let snapshot = if let Some(cached) =
        curve_cache.get_fresh(&candidate.bonding_curve, cache_max_age_ms)
    {
        Ok(SelectedCandidateCurveSnapshot {
            state: cached.state,
            slot: Some(cached.slot),
            observed_at_ms: cached.observed_at_ms,
            source: cached.source,
        })
    } else {
        async {
            let account_data = fetch_account_data_base64(
                &hot.rpc_fallback_url,
                &candidate.bonding_curve,
                parse_env_u64(
                    "RUST_SELECTED_CANDIDATE_6042_COUNTERFACTUAL_RPC_TIMEOUT_MS",
                    250,
                )
                .clamp(50, 1_000),
            )
            .await?;
            let state = decode_bonding_curve_state(&account_data)
                .context("6042 counterfactual curve decode failed")?;
            Ok::<SelectedCandidateCurveSnapshot, anyhow::Error>(SelectedCandidateCurveSnapshot {
                state,
                slot: None,
                observed_at_ms: now_ms().min(u64::MAX as u128) as u64,
                source: "rpc-fallback-6042-counterfactual",
            })
        }
        .await
    };
    let submitted_min_tokens_out_raw = json_u64_at(min_out_plan, &["submittedMinTokensOutRaw"]);
    let protected_min_tokens_out_raw = json_u64_at(min_out_plan, &["protectedMinTokensOutRaw"]);
    let buffered_min_tokens_out_raw = json_u64_at(min_out_plan, &["bufferedMinTokensOutRaw"]);
    match snapshot {
        Ok(snapshot) => {
            let current_quote_tokens_out_raw = quote_buy_tokens_out_raw_main(
                snapshot.state.virtual_sol_reserves as u128,
                snapshot.state.virtual_token_reserves as u128,
                sol_in_lamports as u128,
            )
            .ok()
            .and_then(|value| u64::try_from(value).ok());
            let baseline_mc_sol = estimate_market_cap_sol(&CurveState {
                virtual_token_reserves: candidate.virtual_token_reserves,
                virtual_sol_reserves: candidate.virtual_sol_reserves,
                token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
                complete: false,
                creator: None,
            });
            let current_market_cap_usd = baseline_mc_sol
                .filter(|value| value.is_finite() && *value > 0.0)
                .and_then(|baseline| {
                    estimate_market_cap_sol(&snapshot.state)
                        .map(|current| current * (candidate.entry_market_cap_usd / baseline))
                });
            let would_pass_submitted_min = current_quote_tokens_out_raw
                .zip(submitted_min_tokens_out_raw)
                .map(|(quote, min_out)| quote >= min_out);
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidate6042Counterfactual",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "signature": signature,
                    "bondingCurve": candidate.bonding_curve,
                    "finalitySlot": finality_slot,
                    "counterfactualMs": now_ms().saturating_sub(started_at_ms),
                    "reserveSource": snapshot.source,
                    "reserveSlot": snapshot.slot,
                    "reserveObservedAtMs": snapshot.observed_at_ms,
                    "reserveAgeMs": (now_ms().min(u64::MAX as u128) as u64).saturating_sub(snapshot.observed_at_ms),
                    "shadowEntryMarketCapUsd": candidate.entry_market_cap_usd,
                    "counterfactualMarketCapUsd": current_market_cap_usd,
                    "quotedTokensOutRaw": quoted_tokens_out_raw,
                    "currentQuoteTokensOutRaw": current_quote_tokens_out_raw,
                    "submittedMinTokensOutRaw": submitted_min_tokens_out_raw,
                    "protectedMinTokensOutRaw": protected_min_tokens_out_raw,
                    "bufferedMinTokensOutRaw": buffered_min_tokens_out_raw,
                    "currentVsQuotedDriftBps": current_quote_tokens_out_raw
                        .and_then(|current| fill_drift_bps(current, quoted_tokens_out_raw)),
                    "submittedMinVsCurrentQuotePct": current_quote_tokens_out_raw
                        .zip(submitted_min_tokens_out_raw)
                        .and_then(|(current, min_out)| fill_ratio_pct(min_out, current)),
                    "wouldPassSubmittedMinAtCounterfactualQuote": would_pass_submitted_min,
                    "maxEntryMarketCapUsd": cfg.max_entry_market_cap_usd,
                    "insideMaxEntryMarketCap": cfg.max_entry_market_cap_usd
                        .zip(current_market_cap_usd)
                        .map(|(max_mc, current_mc)| current_mc <= max_mc),
                    "caveat": "Post-finality/current curve sample, not guaranteed exact landing-slot state.",
                }),
            );
        }
        Err(err) => emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidate6042CounterfactualError",
                "atMs": now_ms(),
                "key": candidate.key,
                "mint": candidate.mint,
                "signature": signature,
                "bondingCurve": candidate.bonding_curve,
                "error": err.to_string(),
            }),
        ),
    }
}

async fn flatten_selected_candidate_wallet_position(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    position: &WalletTokenPosition,
) -> Result<()> {
    let sell_slippage_bps = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_WALLET_RECONCILE_SELL_SLIPPAGE_BPS",
        9_500,
    )
    .min(10_000);
    flatten_selected_candidate_wallet_position_with_slippage(
        cfg,
        hot,
        position,
        sell_slippage_bps,
        "rustSelectedCandidateWalletFlattenStart",
        "rustSelectedCandidateWalletFlattenResult",
    )
    .await
}

async fn flatten_selected_candidate_wallet_position_with_slippage(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    position: &WalletTokenPosition,
    sell_slippage_bps: u64,
    start_kind: &'static str,
    result_kind: &'static str,
) -> Result<()> {
    if matches!(position.mint.as_str(), WRAPPED_SOL_MINT | USDC_MINT) {
        return Ok(());
    }
    let bonding_curve = pumpfun_bonding_curve_pda(&position.mint)
        .ok_or_else(|| anyhow::anyhow!("could not derive Pump.fun bonding curve"))?;
    let account_data = fetch_account_data_base64(&hot.rpc_fallback_url, &bonding_curve, 1_000)
        .await
        .context("wallet flatten curve fetch failed")?;
    let state = decode_bonding_curve_state(&account_data)
        .ok_or_else(|| anyhow::anyhow!("wallet flatten curve decode failed"))?;
    if state.complete {
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateWalletFlattenSkipped",
                "atMs": now_ms(),
                "mint": position.mint,
                "bondingCurve": bonding_curve,
                "reason": "bonding_curve_complete",
            }),
        );
        return Ok(());
    }
    let creator = state
        .creator
        .clone()
        .ok_or_else(|| anyhow::anyhow!("wallet flatten curve creator missing"))?;
    let expected_sol_out_lamports = quote_sell_lamports_main(
        state.virtual_token_reserves,
        state.virtual_sol_reserves,
        position.amount_raw,
    )
    .ok_or_else(|| anyhow::anyhow!("wallet flatten quote failed"))?
    .max(1);
    let min_sol_out_lamports = expected_sol_out_lamports
        .saturating_mul(10_000_u64.saturating_sub(sell_slippage_bps))
        / 10_000.max(1);
    let (sell_v2, accounts) = executor::build_pumpfun_exit_sell_template(
        &position.mint,
        &bonding_curve,
        &creator,
        Some(&position.token_program_id),
    )?;
    let blockhash = hot_blockhash(hot).await?;
    let (priority_fee_micro_lamports, jito_tip_lamports, helius_tip_lamports) =
        selected_candidate_sell_fees(hot, cfg.buy_lamports.max(1));
    let command = executor::ExecutePumpfunSellCommand {
        id: format!("wallet-flatten-{}-{}", now_ms(), position.mint),
        route: if sell_v2 {
            "pumpfun-direct-sell-v2".to_string()
        } else {
            "pumpfun-direct-sell".to_string()
        },
        mint: position.mint.clone(),
        tokens_in_raw: position.amount_raw,
        expected_sol_out_lamports,
        min_sol_out_lamports,
        sell_v2,
        accounts: accounts
            .into_iter()
            .map(|account| executor::SourcePumpSwapAccountMeta {
                pubkey: account.pubkey,
                is_signer: account.is_signer,
                is_writable: account.is_writable,
            })
            .collect(),
        blockhash,
        compute_unit_limit: hot.compute_unit_limit,
        priority_fee_micro_lamports,
        jito_tip_lamports,
        helius_tip_lamports,
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
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": start_kind,
            "atMs": now_ms(),
            "mint": position.mint,
            "tokenProgramId": position.token_program_id,
            "amountRaw": position.amount_raw,
            "uiAmount": position.ui_amount,
            "bondingCurve": bonding_curve,
            "expectedSolOutLamports": expected_sol_out_lamports,
            "minSolOutLamports": min_sol_out_lamports,
            "sellSlippageBps": sell_slippage_bps,
            "sellV2": sell_v2,
            "priorityFeeMicroLamports": priority_fee_micro_lamports,
            "jitoTipLamports": jito_tip_lamports,
            "heliusTipLamports": helius_tip_lamports,
        }),
    );
    let started = Instant::now();
    let response = executor::execute_pumpfun_sell_command(command, started).await?;
    let signature = response.signature.clone().unwrap_or_default();
    let finality = if !signature.is_empty() {
        confirm_signature_status(
            &hot.rpc_fallback_url,
            &signature,
            parse_env_u64("RUST_SELECTED_CANDIDATE_SELL_STATUS_TIMEOUT_MS", 2_000),
            parse_env_u64("RUST_SELECTED_CANDIDATE_SELL_STATUS_POLL_MS", 100),
        )
        .await
    } else {
        SignatureStatusCheck {
            status: "missing_signature",
            ok: Some(false),
            confirmation_status: None,
            slot: None,
            error: response.error.clone(),
        }
    };
    let failure_error = response
        .error
        .clone()
        .or_else(|| finality.error.clone())
        .unwrap_or_default();
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": result_kind,
            "atMs": now_ms(),
            "mint": position.mint,
            "tokenProgramId": position.token_program_id,
            "amountRaw": position.amount_raw,
            "expectedSolOutLamports": expected_sol_out_lamports,
            "minSolOutLamports": min_sol_out_lamports,
            "sellSlippageBps": sell_slippage_bps,
            "ok": response.ok,
            "signature": signature,
            "route": response.route,
            "senderPath": response.sender_path,
            "elapsedMs": response.elapsed_ms,
            "buildMs": response.build_ms,
            "broadcastMs": response.broadcast_ms,
            "txBytes": response.tx_bytes,
            "finalityStatus": finality.status,
            "finalityOk": finality.ok,
            "confirmationStatus": finality.confirmation_status,
            "slot": finality.slot,
            "error": failure_error,
        }),
    );
    if response.ok && finality.ok == Some(true) {
        release_selected_candidate_mint_capacity(cfg, &position.mint);
        Ok(())
    } else if parse_env_bool(
        "RUST_SELECTED_CANDIDATE_WALLET_FLATTEN_CHUNK_ON_6023_ENABLED",
        true,
    ) && selected_candidate_exit_failure_should_flatten_immediately(&failure_error)
    {
        flatten_selected_candidate_wallet_position_chunked(cfg, hot, position).await
    } else {
        Err(anyhow::anyhow!("wallet flatten sell did not land ok"))
    }
}

async fn flatten_selected_candidate_wallet_position_chunked(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    position: &WalletTokenPosition,
) -> Result<()> {
    let max_attempts = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_WALLET_FLATTEN_CHUNK_MAX_ATTEMPTS",
        40,
    )
    .clamp(1, 200);
    let chunk_bps =
        parse_env_u64("RUST_SELECTED_CANDIDATE_WALLET_FLATTEN_CHUNK_BPS", 2_500).clamp(1, 10_000);
    let min_chunk_raw =
        parse_env_u64("RUST_SELECTED_CANDIDATE_WALLET_FLATTEN_CHUNK_MIN_RAW", 1).max(1);
    let mut forced_chunk_raw: Option<u64> = None;
    let mut last_error: Option<String> = None;

    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateWalletFlattenChunkedStart",
            "atMs": now_ms(),
            "mint": position.mint,
            "tokenProgramId": position.token_program_id,
            "initialAmountRaw": position.amount_raw,
            "maxAttempts": max_attempts,
            "chunkBps": chunk_bps,
            "minChunkRaw": min_chunk_raw,
        }),
    );

    for attempt in 1..=max_attempts {
        let positions =
            fetch_selected_candidate_wallet_nonzero_token_positions(&hot.rpc_fallback_url).await?;
        let Some(current_position) = positions
            .into_iter()
            .find(|current| current.mint == position.mint && current.amount_raw > 0)
        else {
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateWalletFlattenChunkedFinished",
                    "atMs": now_ms(),
                    "mint": position.mint,
                    "attempts": attempt.saturating_sub(1),
                    "remainingNonzero": false,
                }),
            );
            release_selected_candidate_mint_capacity(cfg, &position.mint);
            return Ok(());
        };

        let bonding_curve = pumpfun_bonding_curve_pda(&current_position.mint)
            .ok_or_else(|| anyhow::anyhow!("could not derive Pump.fun bonding curve"))?;
        let account_data = fetch_account_data_base64(&hot.rpc_fallback_url, &bonding_curve, 1_000)
            .await
            .context("wallet chunk flatten curve fetch failed")?;
        let state = decode_bonding_curve_state(&account_data)
            .ok_or_else(|| anyhow::anyhow!("wallet chunk flatten curve decode failed"))?;
        if state.complete {
            bail!("wallet chunk flatten cannot close completed bonding curve");
        }
        let creator = state
            .creator
            .clone()
            .ok_or_else(|| anyhow::anyhow!("wallet chunk flatten curve creator missing"))?;
        let base_chunk_raw = forced_chunk_raw.unwrap_or_else(|| {
            ((current_position.amount_raw as u128 * chunk_bps as u128) / 10_000)
                .max(min_chunk_raw as u128)
                .min(current_position.amount_raw as u128) as u64
        });
        let tokens_in_raw = base_chunk_raw.min(current_position.amount_raw).max(1);
        let expected_sol_out_lamports = quote_sell_lamports_main(
            state.virtual_token_reserves,
            state.virtual_sol_reserves,
            tokens_in_raw,
        )
        .unwrap_or(1)
        .max(1);
        let (sell_v2, accounts) = executor::build_pumpfun_exit_sell_template(
            &current_position.mint,
            &bonding_curve,
            &creator,
            Some(&current_position.token_program_id),
        )?;
        let blockhash = hot_blockhash(hot).await?;
        let (priority_fee_micro_lamports, jito_tip_lamports, helius_tip_lamports) =
            selected_candidate_sell_fees(hot, cfg.buy_lamports.max(1));
        let command = executor::ExecutePumpfunSellCommand {
            id: format!(
                "wallet-chunk-flatten-{}-{}-{}",
                now_ms(),
                attempt,
                current_position.mint
            ),
            route: if sell_v2 {
                "pumpfun-direct-sell-v2-chunk-flatten".to_string()
            } else {
                "pumpfun-direct-sell-chunk-flatten".to_string()
            },
            mint: current_position.mint.clone(),
            tokens_in_raw,
            expected_sol_out_lamports,
            min_sol_out_lamports: 1,
            sell_v2,
            accounts: accounts
                .into_iter()
                .map(|account| executor::SourcePumpSwapAccountMeta {
                    pubkey: account.pubkey,
                    is_signer: account.is_signer,
                    is_writable: account.is_writable,
                })
                .collect(),
            blockhash,
            compute_unit_limit: hot.compute_unit_limit,
            priority_fee_micro_lamports,
            jito_tip_lamports,
            helius_tip_lamports,
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
        let started = Instant::now();
        let response = executor::execute_pumpfun_sell_command(command, started).await?;
        let signature = response.signature.clone().unwrap_or_default();
        let finality = if !signature.is_empty() {
            confirm_signature_status(
                &hot.rpc_fallback_url,
                &signature,
                parse_env_u64("RUST_SELECTED_CANDIDATE_SELL_STATUS_TIMEOUT_MS", 2_000),
                parse_env_u64("RUST_SELECTED_CANDIDATE_SELL_STATUS_POLL_MS", 100),
            )
            .await
        } else {
            SignatureStatusCheck {
                status: "missing_signature",
                ok: Some(false),
                confirmation_status: None,
                slot: None,
                error: response.error.clone(),
            }
        };
        let failure_error = response
            .error
            .clone()
            .or_else(|| finality.error.clone())
            .unwrap_or_default();
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateWalletFlattenChunkResult",
                "atMs": now_ms(),
                "mint": current_position.mint,
                "attempt": attempt,
                "amountBeforeRaw": current_position.amount_raw,
                "tokensInRaw": tokens_in_raw,
                "expectedSolOutLamports": expected_sol_out_lamports,
                "priorityFeeMicroLamports": priority_fee_micro_lamports,
                "jitoTipLamports": jito_tip_lamports,
                "heliusTipLamports": helius_tip_lamports,
                "ok": response.ok,
                "signature": signature,
                "route": response.route,
                "senderPath": response.sender_path,
                "elapsedMs": response.elapsed_ms,
                "buildMs": response.build_ms,
                "broadcastMs": response.broadcast_ms,
                "finalityStatus": finality.status,
                "finalityOk": finality.ok,
                "confirmationStatus": finality.confirmation_status,
                "slot": finality.slot,
                "error": failure_error,
            }),
        );
        if response.ok && finality.ok == Some(true) {
            forced_chunk_raw = None;
            sleep(Duration::from_millis(100)).await;
            continue;
        }
        last_error = Some(failure_error.clone());
        if selected_candidate_exit_failure_should_flatten_immediately(&failure_error)
            && tokens_in_raw > min_chunk_raw
        {
            forced_chunk_raw = Some((tokens_in_raw / 2).max(min_chunk_raw));
            sleep(Duration::from_millis(100)).await;
            continue;
        }
        break;
    }

    let remaining =
        fetch_selected_candidate_wallet_nonzero_token_positions(&hot.rpc_fallback_url).await?;
    let remaining_for_mint = remaining
        .into_iter()
        .find(|current| current.mint == position.mint && current.amount_raw > 0);
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateWalletFlattenChunkedUnfinished",
            "atMs": now_ms(),
            "mint": position.mint,
            "remainingAmountRaw": remaining_for_mint.as_ref().map(|p| p.amount_raw),
            "remainingUiAmount": remaining_for_mint.as_ref().and_then(|p| p.ui_amount.clone()),
            "lastError": last_error,
        }),
    );
    if remaining_for_mint.is_none() {
        release_selected_candidate_mint_capacity(cfg, &position.mint);
        Ok(())
    } else {
        Err(anyhow::anyhow!("wallet chunk flatten left nonzero balance"))
    }
}

fn quote_sell_lamports_main(
    virtual_token_reserves: u64,
    virtual_sol_reserves: u64,
    tokens_in_raw: u64,
) -> Option<u64> {
    if tokens_in_raw == 0 || virtual_token_reserves == 0 || virtual_sol_reserves == 0 {
        return None;
    }
    let pre_sol = virtual_sol_reserves as u128;
    let pre_tokens = virtual_token_reserves as u128;
    let post_tokens = pre_tokens.checked_add(tokens_in_raw as u128)?;
    let k = pre_sol.checked_mul(pre_tokens)?;
    let post_sol = k / post_tokens;
    let gross = pre_sol.checked_sub(post_sol)?;
    let fee = gross.checked_mul(PUMP_FUN_SELL_TRADE_FEE_BPS)? / 10_000;
    (gross > fee).then_some((gross - fee).min(u64::MAX as u128) as u64)
}

async fn fetch_selected_candidate_wallet_nonzero_token_positions(
    rpc_url: &str,
) -> Result<Vec<WalletTokenPosition>> {
    let owner = selected_candidate_wallet_pubkey()?.to_string();
    let mut positions = Vec::new();
    for token_program_id in [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID] {
        let body = json!({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                owner,
                { "programId": token_program_id },
                { "encoding": "jsonParsed", "commitment": "processed" }
            ],
        });
        let response = selected_candidate_rpc_client()
            .post(rpc_url)
            .timeout(Duration::from_millis(1_500))
            .json(&body)
            .send()
            .await
            .context("wallet reconcile getTokenAccountsByOwner send failed")?;
        let json = response
            .json::<JsonRpcResponse>()
            .await
            .context("wallet reconcile getTokenAccountsByOwner parse failed")?;
        if let Some(error) = json.error {
            let code = error.code.map(|c| format!(" code {c}")).unwrap_or_default();
            bail!(
                "{}{}",
                error
                    .message
                    .unwrap_or_else(|| "getTokenAccountsByOwner json-rpc error".to_string()),
                code
            );
        }
        let rows = json
            .result
            .and_then(|result| result.get("value").cloned())
            .and_then(|value| value.as_array().cloned())
            .unwrap_or_default();
        for row in rows {
            let Some(info) = row
                .get("account")
                .and_then(|v| v.get("data"))
                .and_then(|v| v.get("parsed"))
                .and_then(|v| v.get("info"))
            else {
                continue;
            };
            let Some(mint) = info.get("mint").and_then(Value::as_str) else {
                continue;
            };
            if matches!(mint, WRAPPED_SOL_MINT | USDC_MINT) {
                continue;
            }
            let amount_raw = info
                .get("tokenAmount")
                .and_then(|value| value.get("amount"))
                .and_then(Value::as_str)
                .and_then(|amount| amount.parse::<u64>().ok())
                .unwrap_or(0);
            if amount_raw == 0 {
                continue;
            }
            positions.push(WalletTokenPosition {
                mint: mint.to_string(),
                token_program_id: token_program_id.to_string(),
                amount_raw,
                ui_amount: info
                    .get("tokenAmount")
                    .and_then(|value| value.get("uiAmountString"))
                    .and_then(Value::as_str)
                    .map(str::to_string),
            });
        }
    }
    positions.sort_by(|a, b| a.mint.cmp(&b.mint));
    Ok(positions)
}

async fn handle_failed_exit(
    registry: &exit_watcher::ExitWatcherRegistry,
    action_tx: &UnboundedSender<exit_watcher::ExitAction>,
    action: &exit_watcher::ExitAction,
    selected_cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    curve_cache: &CandidateCurveCache,
    exit_failure_counts: &mut HashMap<String, u64>,
    max_finality_failures: u64,
    flatten_after_failures: bool,
    error: String,
) {
    let failures = exit_failure_counts
        .entry(action.mint.clone())
        .and_modify(|count| *count = count.saturating_add(1))
        .or_insert(1);
    let failure_count = *failures;

    emit_json_event(
        selected_cfg.audit_file.as_deref(),
        json!({
            "kind": "rustExitSellFailureCount",
            "mint": action.mint,
            "sequence": action.sequence,
            "reason": action.reason.as_str(),
            "failureCount": failure_count,
            "maxFinalityFailures": max_finality_failures,
            "exactBalanceProof": action.exact_balance_proof,
            "safeSellTokensRaw": action.safe_sell_tokens_raw,
            "preExactGuaranteedSell": !action.exact_balance_proof && action.safe_sell_tokens_raw.is_some(),
            "error": &error,
        }),
    );

    let balance_state_mismatch = selected_candidate_exit_failure_should_flatten_immediately(&error);
    let pre_exact_guaranteed_sell =
        !action.exact_balance_proof && action.safe_sell_tokens_raw.is_some();
    let retry_pre_exact_state_mismatch = selected_candidate_should_retry_pre_exact_state_mismatch(
        action,
        failure_count,
        max_finality_failures,
        &error,
    );
    let retry_profit_6003 =
        selected_candidate_should_retry_profit_6003(action, failure_count, &error);
    let immediate_wallet_flatten = flatten_after_failures
        && balance_state_mismatch
        && !retry_pre_exact_state_mismatch
        && !retry_profit_6003;
    if retry_pre_exact_state_mismatch {
        emit_json_event(
            selected_cfg.audit_file.as_deref(),
            json!({
                "kind": "rustExitPreExactSafeSellRetry",
                "atMs": now_ms(),
                "mint": action.mint,
                "sequence": action.sequence,
                "reason": action.reason.as_str(),
                "failureCount": failure_count,
                "maxFinalityFailures": max_finality_failures,
                "safeSellTokensRaw": action.safe_sell_tokens_raw,
                "error": &error,
                "note": "pre-exact guaranteed-min-out sell hit account/balance visibility race; retry without cancelling the watch",
            }),
        );
    }
    if retry_profit_6003 {
        let retry_known_at_ms = now_ms().min(u64::MAX as u128) as u64;
        let tp_6003_salvage_min_realized_pnl_pct = parse_env_optional_f64(
            "RUST_SELECTED_CANDIDATE_EXIT_TP_6003_SALVAGE_MIN_REALIZED_PNL_PCT",
        )
        .or_else(|| {
            parse_env_optional_f64("RUST_SELECTED_CANDIDATE_EXIT_TP_6003_SALVAGE_MIN_PNL_PCT")
        })
        .or(action.tp_6003_salvage_min_realized_pnl_pct)
        .or(action.min_acceptable_take_profit_pnl_pct);
        emit_json_event(
            selected_cfg.audit_file.as_deref(),
            json!({
                "kind": "rustExitProfit6003ProtectedRetry",
                "atMs": retry_known_at_ms,
                "mint": action.mint,
                "sequence": action.sequence,
                "reason": action.reason.as_str(),
                "failureCount": failure_count,
                "maxFinalityFailures": max_finality_failures,
                "exactBalanceProof": action.exact_balance_proof,
                "tp6003SalvageMinRealizedPnlPct": tp_6003_salvage_min_realized_pnl_pct,
                "minAcceptableTakeProfitPnlPct": action.min_acceptable_take_profit_pnl_pct,
                "error": &error,
                "note": "profit exit hit Custom 6003; retry once with latest local curve repricing before circuit flatten",
            }),
        );
        if let Some(min_pnl) = tp_6003_salvage_min_realized_pnl_pct {
            let max_age_ms = parse_env_u64(
                "RUST_SELECTED_CANDIDATE_EXIT_TP_6003_RETRY_CURVE_CACHE_MAX_AGE_MS",
                parse_env_u64(
                    "RUST_SELECTED_CANDIDATE_EXIT_SEND_TIME_CURVE_CACHE_MAX_AGE_MS",
                    selected_cfg.curve_cache_max_age_ms,
                ),
            )
            .clamp(1, 2_000);
            if let Some(cached) =
                curve_cache.get_fresh_yellowstone(&action.bonding_curve, max_age_ms)
            {
                let retry_quote = quote_sell_lamports_main(
                    cached.state.virtual_token_reserves,
                    cached.state.virtual_sol_reserves,
                    action.tokens_in_raw,
                );
                let retry_fee_adjusted_pnl_pct = retry_quote.map(|quote| {
                    selected_candidate_fee_adjusted_pnl_pct(
                        quote,
                        action.cost_basis_lamports,
                        action.decision_execution_fee_lamports,
                    )
                });
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitProfit6003ImmediateRecoveryQuote",
                        "atMs": now_ms(),
                        "mint": action.mint,
                        "sequence": action.sequence,
                        "failureCount": failure_count,
                        "quoteSource": cached.source,
                        "quoteSlot": cached.slot,
                        "quoteObservedAtMs": cached.observed_at_ms,
                        "quoteAgeMs": (now_ms().min(u64::MAX as u128) as u64)
                            .saturating_sub(cached.observed_at_ms),
                        "maxAgeMs": max_age_ms,
                        "retryExpectedSolOutLamports": retry_quote,
                        "retryFeeAdjustedPnlPct": retry_fee_adjusted_pnl_pct,
                        "tp6003SalvageMinRealizedPnlPct": min_pnl,
                    }),
                );
                if retry_quote.is_some()
                    && retry_fee_adjusted_pnl_pct.unwrap_or(f64::NEG_INFINITY) >= min_pnl
                {
                    let retry_now_ms = now_ms().min(u64::MAX as u128) as u64;
                    let retry_state = exit_watcher::CurveState {
                        virtual_token_reserves: cached.state.virtual_token_reserves,
                        virtual_sol_reserves: cached.state.virtual_sol_reserves,
                        complete: cached.state.complete,
                    };
                    let (status, follow_up) = registry.retry_profit_6003_now(
                        format!(
                            "rust-exit-profit-6003-retry-{}-{}",
                            action.mint, action.sequence
                        ),
                        action.mint.clone(),
                        action.sequence,
                        retry_state,
                        cached.slot,
                        retry_now_ms,
                        min_pnl,
                    );
                    emit_json_event(
                        selected_cfg.audit_file.as_deref(),
                        json!({
                            "kind": "rustExitProfit6003ImmediateRecoveryDecision",
                            "atMs": now_ms(),
                            "mint": status.mint,
                            "previousSequence": action.sequence,
                            "ok": status.ok,
                            "phase": status.phase,
                            "actionPending": status.action_pending,
                            "error": status.error,
                            "retryQueued": follow_up.is_some(),
                            "knownToRetryQueuedMs": (now_ms().min(u64::MAX as u128) as u64)
                                .saturating_sub(retry_known_at_ms),
                        }),
                    );
                    if let Some(follow_up) = follow_up {
                        let _ = action_tx.send(follow_up);
                    }
                    return;
                }
            } else {
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitProfit6003ImmediateRecoveryQuoteMiss",
                        "atMs": now_ms(),
                        "mint": action.mint,
                        "sequence": action.sequence,
                        "bondingCurve": action.bonding_curve,
                        "maxAgeMs": max_age_ms,
                    }),
                );
            }
        }
        reopen_failed_exit_without_cooldown(
            registry,
            action,
            selected_cfg.audit_file.as_deref(),
            error,
        );
        return;
    }
    if selected_candidate_should_try_pre_exact_profit_state_mismatch_recovery(
        action,
        failure_count,
        &error,
    ) {
        match try_pre_exact_profit_state_mismatch_exact_cache_recovery(
            selected_cfg,
            hot,
            &action.mint,
            action.sequence,
        )
        .await
        {
            Ok(true) => {
                exit_failure_counts.remove(&action.mint);
                let status = registry.cancel(exit_watcher::CancelPumpfunExitWatchCommand {
                    id: format!(
                        "rust-exit-pre-exact-3012-recovery-ack-{}-{}",
                        action.mint, action.sequence
                    ),
                    mint: action.mint.clone(),
                    reason: Some(
                        "pre-exact profit state mismatch recovered from exact local balance cache"
                            .to_string(),
                    ),
                });
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitPreExactProfitStateMismatchRecoveryAck",
                        "atMs": now_ms(),
                        "ok": status.ok,
                        "mint": status.mint,
                        "phase": status.phase,
                        "actionPending": status.action_pending,
                        "error": status.error,
                        "closedBy": "exact_cache_profit_recovery_success",
                        "suppressedFollowUp": true,
                    }),
                );
                release_selected_candidate_mint_capacity(selected_cfg, &action.mint);
                return;
            }
            Ok(false) => {
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitPreExactProfitStateMismatchRecoverySkipped",
                        "atMs": now_ms(),
                        "mint": action.mint,
                        "sequence": action.sequence,
                        "reason": "exact_local_balance_unavailable_or_zero",
                    }),
                );
            }
            Err(err) => {
                emit_json_event(
                    selected_cfg.audit_file.as_deref(),
                    json!({
                        "kind": "rustExitPreExactProfitStateMismatchRecoveryFailed",
                        "atMs": now_ms(),
                        "mint": action.mint,
                        "sequence": action.sequence,
                        "error": err.to_string(),
                    }),
                );
            }
        }
    }
    if failure_count >= max_finality_failures || immediate_wallet_flatten {
        emit_json_event(
            selected_cfg.audit_file.as_deref(),
            json!({
                "kind": "rustExitSellCircuitBreakerOpen",
                "atMs": now_ms(),
                "mint": action.mint,
                "sequence": action.sequence,
                "reason": action.reason.as_str(),
                "failureCount": failure_count,
                "maxFinalityFailures": max_finality_failures,
                "flattenAfterFailures": flatten_after_failures,
                "immediateWalletFlatten": immediate_wallet_flatten,
                "balanceStateMismatch": balance_state_mismatch,
                "preExactGuaranteedSell": pre_exact_guaranteed_sell,
            }),
        );
        if flatten_after_failures {
            match flatten_selected_candidate_wallet_mint_with_retries(
                selected_cfg,
                hot,
                &action.mint,
                action.sequence,
            )
            .await
            {
                Ok(true) => {
                    exit_failure_counts.remove(&action.mint);
                    let status = registry.cancel(exit_watcher::CancelPumpfunExitWatchCommand {
                        id: format!(
                            "rust-exit-circuit-flatten-ack-{}-{}",
                            action.mint, action.sequence
                        ),
                        mint: action.mint.clone(),
                        reason: Some(
                            "circuit breaker wallet flatten succeeded; close stale pending exit"
                                .to_string(),
                        ),
                    });
                    emit_json_event(
                        selected_cfg.audit_file.as_deref(),
                        json!({
                            "kind": "rustExitSellCircuitBreakerFlattenAck",
                            "atMs": now_ms(),
                            "ok": status.ok,
                            "mint": status.mint,
                            "phase": status.phase,
                            "actionPending": status.action_pending,
                            "error": status.error,
                            "closedBy": "wallet_flatten_success",
                            "suppressedFollowUp": true,
                        }),
                    );
                    release_selected_candidate_mint_capacity(selected_cfg, &action.mint);
                    return;
                }
                Ok(false) => {
                    let status = registry.cancel(exit_watcher::CancelPumpfunExitWatchCommand {
                        id: format!(
                            "rust-exit-circuit-wallet-flat-cancel-{}-{}",
                            action.mint, action.sequence
                        ),
                        mint: action.mint.clone(),
                        reason: Some(
                            "circuit breaker wallet flatten found no on-chain position".to_string(),
                        ),
                    });
                    exit_failure_counts.remove(&action.mint);
                    release_selected_candidate_mint_capacity(selected_cfg, &action.mint);
                    emit_json_event(
                        selected_cfg.audit_file.as_deref(),
                        json!({
                            "kind": "rustExitSellCircuitBreakerFlattenSkipped",
                            "atMs": now_ms(),
                            "mint": action.mint,
                            "sequence": action.sequence,
                            "reason": "wallet_position_not_found",
                            "cancelOk": status.ok,
                            "cancelPhase": status.phase,
                            "cancelActionPending": status.action_pending,
                            "cancelError": status.error,
                        }),
                    );
                    return;
                }
                Err(err) => {
                    emit_json_event(
                        selected_cfg.audit_file.as_deref(),
                        json!({
                            "kind": "rustExitSellCircuitBreakerFlattenFailed",
                            "atMs": now_ms(),
                            "mint": action.mint,
                            "sequence": action.sequence,
                            "error": err.to_string(),
                        }),
                    );
                }
            }
        }
        // Keep the registry pending instead of reopening the same failing sell.
        // This freezes the mint for manual inspection and prevents fee burn.
        return;
    }

    reopen_failed_exit(
        registry,
        action_tx,
        action,
        selected_cfg.audit_file.as_deref(),
        error,
    );
}

fn reopen_failed_exit(
    registry: &exit_watcher::ExitWatcherRegistry,
    action_tx: &UnboundedSender<exit_watcher::ExitAction>,
    action: &exit_watcher::ExitAction,
    audit_file: Option<&str>,
    error: String,
) {
    let (_, follow_up) = registry.ack(exit_watcher::AckPumpfunExitSellCommand {
        id: format!(
            "rust-exit-broadcast-fail-{}-{}",
            action.mint, action.sequence
        ),
        mint: action.mint.clone(),
        sequence: action.sequence,
        ok: false,
        actual_sol_out_lamports: None,
        error: Some(error.clone()),
    });
    if let Some(follow_up) = follow_up {
        let _ = action_tx.send(follow_up);
    }
    emit_json_event(
        audit_file,
        json!({
            "kind": "rustExitSellFailed",
            "mint": action.mint,
            "sequence": action.sequence,
            "reason": action.reason.as_str(),
            "error": error,
        }),
    );
}

fn reopen_failed_exit_without_cooldown(
    registry: &exit_watcher::ExitWatcherRegistry,
    action: &exit_watcher::ExitAction,
    audit_file: Option<&str>,
    error: String,
) {
    let status = registry.reopen_failed_exit_without_cooldown(
        format!(
            "rust-exit-broadcast-fail-no-cooldown-{}-{}",
            action.mint, action.sequence
        ),
        action.mint.clone(),
        action.sequence,
    );
    emit_json_event(
        audit_file,
        json!({
            "kind": "rustExitSellFailedNoCooldown",
            "atMs": now_ms(),
            "mint": action.mint,
            "sequence": action.sequence,
            "reason": action.reason.as_str(),
            "ok": status.ok,
            "phase": status.phase,
            "actionPending": status.action_pending,
            "error": error,
            "ackError": status.error,
        }),
    );
}

fn selected_candidate_exit_failure_should_flatten_immediately(error: &str) -> bool {
    let lower = error.to_ascii_lowercase();
    lower.contains("custom\":6023")
        || lower.contains("custom(6023)")
        || lower.contains("custom 6023")
        || lower.contains("custom\": 6023")
        || lower.contains("custom\":3012")
        || lower.contains("custom(3012)")
        || lower.contains("custom 3012")
        || lower.contains("custom\": 3012")
        || lower.contains("custom\":6074")
        || lower.contains("custom(6074)")
        || lower.contains("custom 6074")
        || lower.contains("custom\": 6074")
}

fn selected_candidate_buy_finality_error_kind(error: Option<&str>) -> &'static str {
    let Some(error) = error else {
        return "buy_finality_error";
    };
    let lower = error.to_ascii_lowercase();
    if lower.contains("custom\":6042")
        || lower.contains("custom\": 6042")
        || lower.contains("custom(6042)")
        || lower.contains("custom 6042")
    {
        "buy_finality_custom_6042"
    } else {
        "buy_finality_error"
    }
}

fn selected_candidate_buy_failure_accounting_bucket(finality_error_kind: &str) -> &'static str {
    if finality_error_kind == "buy_finality_custom_6042" {
        "protected_missed_fill"
    } else {
        "failed_buy_no_position"
    }
}

fn selected_candidate_exit_failure_is_custom_6003(error: &str) -> bool {
    let lower = error.to_ascii_lowercase();
    lower.contains("custom\":6003")
        || lower.contains("custom\": 6003")
        || lower.contains("custom(6003)")
        || lower.contains("custom 6003")
}

fn selected_candidate_should_retry_profit_6003(
    action: &exit_watcher::ExitAction,
    failure_count: u64,
    error: &str,
) -> bool {
    if !parse_env_bool("RUST_SELECTED_CANDIDATE_RETRY_TP_6003_BEFORE_FLATTEN", true) {
        return false;
    }
    let max_retries =
        parse_env_u64("RUST_SELECTED_CANDIDATE_TP_6003_PROTECTED_RETRIES", 1).clamp(0, 10);
    max_retries > 0
        && failure_count <= max_retries
        && selected_candidate_exit_reason_is_profit_exit(action.reason)
        && selected_candidate_exit_failure_is_custom_6003(error)
}

fn selected_candidate_should_retry_pre_exact_state_mismatch(
    action: &exit_watcher::ExitAction,
    failure_count: u64,
    max_finality_failures: u64,
    error: &str,
) -> bool {
    parse_env_bool(
        "RUST_SELECTED_CANDIDATE_RETRY_PRE_EXACT_STATE_MISMATCH",
        false,
    ) && !action.exact_balance_proof
        && action.safe_sell_tokens_raw.is_some()
        && selected_candidate_exit_failure_should_flatten_immediately(error)
        && failure_count < max_finality_failures
}

fn selected_candidate_should_try_pre_exact_profit_state_mismatch_recovery(
    action: &exit_watcher::ExitAction,
    failure_count: u64,
    error: &str,
) -> bool {
    parse_env_bool(
        "RUST_SELECTED_CANDIDATE_PRE_EXACT_PROFIT_STATE_MISMATCH_EXACT_CACHE_RECOVERY_ENABLED",
        true,
    ) && failure_count == 1
        && !action.exact_balance_proof
        && action.safe_sell_tokens_raw.is_some()
        && selected_candidate_exit_reason_is_profit_exit(action.reason)
        && selected_candidate_exit_failure_should_flatten_immediately(error)
}

async fn try_pre_exact_profit_state_mismatch_exact_cache_recovery(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    mint: &str,
    sequence: u64,
) -> Result<bool> {
    let cache_wait_ms = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_PRE_EXACT_PROFIT_STATE_MISMATCH_CACHE_WAIT_MS",
        125,
    )
    .min(2_000);
    let cache_max_age_ms = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_PRE_EXACT_PROFIT_STATE_MISMATCH_CACHE_MAX_AGE_MS",
        cfg.balance_cache_max_age_ms.max(2_000),
    )
    .max(100);
    let sell_slippage_bps = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_PRE_EXACT_PROFIT_STATE_MISMATCH_SELL_SLIPPAGE_BPS",
        2_500,
    )
    .min(10_000);
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustExitPreExactProfitStateMismatchRecoveryStart",
            "atMs": now_ms(),
            "mint": mint,
            "sequence": sequence,
            "cacheWaitMs": cache_wait_ms,
            "cacheMaxAgeMs": cache_max_age_ms,
            "sellSlippageBps": sell_slippage_bps,
        }),
    );
    let Some(cached) = wait_selected_candidate_fresh_exact_balance_by_mint(
        cfg,
        mint,
        cache_wait_ms,
        cache_max_age_ms,
    )
    .await
    else {
        return Ok(false);
    };
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustExitPreExactProfitStateMismatchRecoveryExactCache",
            "atMs": now_ms(),
            "mint": mint,
            "sequence": sequence,
            "ata": cached.ata,
            "tokenProgramId": cached.token_program_id,
            "amountRaw": cached.amount_raw,
            "slot": cached.slot,
            "observedAtMs": cached.observed_at_ms,
            "balanceSource": cached.source,
        }),
    );
    if cached.amount_raw == 0 {
        return Ok(false);
    }
    let position = WalletTokenPosition {
        mint: cached.mint,
        token_program_id: cached.token_program_id,
        amount_raw: cached.amount_raw,
        ui_amount: None,
    };
    flatten_selected_candidate_wallet_position_with_slippage(
        cfg,
        hot,
        &position,
        sell_slippage_bps,
        "rustExitPreExactProfitStateMismatchRecoverySellStart",
        "rustExitPreExactProfitStateMismatchRecoverySellResult",
    )
    .await?;
    Ok(true)
}

async fn flatten_selected_candidate_wallet_mint(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    mint: &str,
) -> Result<bool> {
    let positions =
        fetch_selected_candidate_wallet_nonzero_token_positions(&hot.rpc_fallback_url).await?;
    let Some(position) = positions
        .into_iter()
        .find(|position| position.mint == mint && position.amount_raw > 0)
    else {
        let cache_wait_ms = parse_env_u64(
            "RUST_SELECTED_CANDIDATE_WALLET_FLATTEN_LOCAL_CACHE_WAIT_MS",
            1_500,
        )
        .min(10_000);
        let cache_max_age_ms = parse_env_u64(
            "RUST_SELECTED_CANDIDATE_WALLET_FLATTEN_LOCAL_CACHE_MAX_AGE_MS",
            cfg.balance_cache_max_age_ms.max(2_000),
        )
        .max(250);
        if let Some(cached) = wait_selected_candidate_fresh_exact_balance_by_mint(
            cfg,
            mint,
            cache_wait_ms,
            cache_max_age_ms,
        )
        .await
        {
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateWalletFlattenRpcMissLocalCacheHit",
                    "atMs": now_ms(),
                    "mint": mint,
                    "ata": cached.ata,
                    "tokenProgramId": cached.token_program_id,
                    "amountRaw": cached.amount_raw,
                    "slot": cached.slot,
                    "observedAtMs": cached.observed_at_ms,
                    "balanceSource": cached.source,
                    "waitMs": cache_wait_ms,
                    "maxAgeMs": cache_max_age_ms,
                    "reason": if cached.amount_raw > 0 {
                        "rpc_position_not_found_but_local_cache_nonzero"
                    } else {
                        "rpc_position_not_found_and_local_cache_zero"
                    },
                }),
            );
            if cached.amount_raw == 0 {
                return Ok(false);
            }
            let position = WalletTokenPosition {
                mint: cached.mint,
                token_program_id: cached.token_program_id,
                amount_raw: cached.amount_raw,
                ui_amount: None,
            };
            flatten_selected_candidate_wallet_position(cfg, hot, &position).await?;
            return Ok(true);
        }
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateWalletFlattenRpcMissLocalCacheMissing",
                "atMs": now_ms(),
                "mint": mint,
                "waitMs": cache_wait_ms,
                "maxAgeMs": cache_max_age_ms,
                "reason": "rpc_position_not_found_and_local_cache_unsettled",
            }),
        );
        return Ok(false);
    };
    flatten_selected_candidate_wallet_position(cfg, hot, &position).await?;
    Ok(true)
}

async fn flatten_selected_candidate_wallet_mint_with_retries(
    cfg: &SelectedCandidateSniperConfig,
    hot: &HotSniperConfig,
    mint: &str,
    sequence: u64,
) -> Result<bool> {
    let attempts =
        parse_env_u64("RUST_SELECTED_CANDIDATE_WALLET_FLATTEN_RETRY_ATTEMPTS", 5).clamp(1, 25);
    let delay_ms = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_WALLET_FLATTEN_RETRY_DELAY_MS",
        1_250,
    )
    .min(10_000);
    let mut last_error: Option<String> = None;

    for attempt in 1..=attempts {
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateWalletFlattenRetryAttempt",
                "atMs": now_ms(),
                "mint": mint,
                "sequence": sequence,
                "attempt": attempt,
                "maxAttempts": attempts,
            }),
        );

        match flatten_selected_candidate_wallet_mint(cfg, hot, mint).await {
            Ok(result) => {
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateWalletFlattenRetryResolved",
                        "atMs": now_ms(),
                        "mint": mint,
                        "sequence": sequence,
                        "attempt": attempt,
                        "maxAttempts": attempts,
                        "walletPositionFound": result,
                    }),
                );
                return Ok(result);
            }
            Err(err) => {
                let error = err.to_string();
                last_error = Some(error.clone());
                emit_selected_candidate_sniper_event(
                    cfg,
                    json!({
                        "kind": "rustSelectedCandidateWalletFlattenRetryFailed",
                        "atMs": now_ms(),
                        "mint": mint,
                        "sequence": sequence,
                        "attempt": attempt,
                        "maxAttempts": attempts,
                        "error": error,
                    }),
                );
                if attempt < attempts {
                    sleep(Duration::from_millis(delay_ms)).await;
                }
            }
        }
    }

    Err(anyhow::anyhow!(
        "wallet flatten retries exhausted{}",
        last_error
            .map(|error| format!(": {error}"))
            .unwrap_or_default()
    ))
}

async fn wait_selected_candidate_fresh_exact_balance_by_mint(
    cfg: &SelectedCandidateSniperConfig,
    mint: &str,
    timeout_ms: u64,
    max_age_ms: u64,
) -> Option<CachedTokenAccountBalance> {
    let timeout_ms = timeout_ms.max(1);
    let deadline = Instant::now() + Duration::from_millis(timeout_ms);
    loop {
        if let Some(cached) = cfg.balance_cache.get_fresh_exact_by_mint(mint, max_age_ms) {
            return Some(cached);
        }
        if Instant::now() >= deadline {
            return None;
        }
        sleep(Duration::from_millis(25)).await;
    }
}

async fn run_once(
    cfg: &Config,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
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

    let tracked_wallets = cfg.tracked_wallets.clone();
    // Keep the request side open after the initial wallet filter. Some
    // Yellowstone providers close the subscription when the request stream
    // reaches EOF, which would create reconnect churn and gaps in detection.
    let requests =
        stream::once(async move { build_request(&tracked_wallets, &[]) }).chain(stream::pending());
    let mut stream = client.subscribe(Request::new(requests)).await?.into_inner();
    eprintln!("rust-sidecar: subscribed");

    while let Some(update) = stream.next().await {
        let update = update?;
        match update.update_oneof {
            Some(UpdateOneof::Transaction(tx_update)) => {
                let Some(info) = tx_update.transaction else {
                    continue;
                };
                if let Some(line) = extract_swap(
                    &info,
                    &cfg.tracked_wallets,
                    Some(tx_update.slot),
                    Some(info.index),
                ) {
                    emit_detected_line(cfg, line, exit_watcher, exit_actions).await?;
                }
            }
            _ => {}
        }
    }

    Ok(())
}

async fn run_exit_curve_account_stream(
    cfg: &Config,
    registry: &exit_watcher::ExitWatcherRegistry,
    exit_actions: &UnboundedSender<exit_watcher::ExitAction>,
) -> Result<()> {
    loop {
        let curves = registry.bonding_curves();
        if curves.is_empty() {
            sleep(Duration::from_millis(
                cfg.exit_subscription_refresh_ms.max(25),
            ))
            .await;
            continue;
        }

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

        let requested_curves = curves.clone();
        let tracked_wallets = cfg.tracked_wallets.clone();
        // Keep the account request stream open as well. A finite request
        // stream makes Yellowstone close the subscription after the initial
        // filter, which looks like a healthy reconnect loop but produces no
        // reliable curve marks.
        // Shyft requires at least one transaction filter on every request,
        // even when the account filter is the only feed we consume here.
        // Keep it narrowly scoped to the configured tracked wallet so this
        // does not turn the account stream into an unbounded transaction feed.
        let requests =
            stream::once(async move { build_request(&tracked_wallets, &requested_curves) })
                .chain(stream::pending());
        let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
        eprintln!(
            "rust-sidecar: exit curve-account subscription active curves={}",
            curves.len()
        );

        loop {
            let next = timeout(
                Duration::from_millis(cfg.exit_subscription_refresh_ms.max(25)),
                updates.message(),
            )
            .await;
            let current_curves = registry.bonding_curves();
            if current_curves != curves {
                eprintln!(
                    "rust-sidecar: exit curve-account subscription refreshing curves={} -> {}",
                    curves.len(),
                    current_curves.len()
                );
                break;
            }

            let update = match next {
                Ok(Ok(Some(update))) => update,
                Ok(Ok(None)) => return Ok(()),
                Ok(Err(error)) => return Err(error.into()),
                Err(_) => continue,
            };
            handle_exit_curve_account_update(update, registry, exit_actions, cfg)?;
        }
    }
}

async fn run_exit_curve_account_dynamic_stream(
    cfg: &Config,
    registry: &exit_watcher::ExitWatcherRegistry,
    exit_actions: &UnboundedSender<exit_watcher::ExitAction>,
) -> Result<()> {
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

    let tracked_wallets = cfg.tracked_wallets.clone();
    let initial_curves = registry.bonding_curves();
    let (request_tx, request_rx) = mpsc::unbounded_channel::<SubscribeRequest>();
    request_tx
        .send(build_request(&tracked_wallets, &initial_curves))
        .context("dynamic exit curve account initial request send failed")?;

    let refresh_request_tx = request_tx.clone();
    let refresh_registry = registry.clone();
    let refresh_tracked_wallets = tracked_wallets.clone();
    let selected_cfg = cfg.selected_candidate_sniper.clone();
    let refresh_ms = cfg.exit_subscription_refresh_ms.max(10);
    tokio::spawn(async move {
        let mut last_curves = initial_curves;
        loop {
            sleep(Duration::from_millis(refresh_ms)).await;
            let current_curves = refresh_registry.bonding_curves();
            if current_curves == last_curves {
                continue;
            }
            if refresh_request_tx
                .send(build_request(&refresh_tracked_wallets, &current_curves))
                .is_err()
            {
                break;
            }
            emit_selected_candidate_sniper_event(
                &selected_cfg,
                json!({
                    "kind": "rustExitCurveAccountDynamicRefresh",
                    "atMs": now_ms(),
                    "oldCurves": last_curves.len(),
                    "newCurves": current_curves.len(),
                    "refreshMs": refresh_ms,
                }),
            );
            last_curves = current_curves;
        }
    });
    let _request_tx_keepalive = request_tx;
    let requests = stream::unfold(request_rx, |mut rx| async {
        rx.recv().await.map(|req| (req, rx))
    });
    let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
    emit_selected_candidate_sniper_event(
        &cfg.selected_candidate_sniper,
        json!({
            "kind": "rustExitCurveAccountDynamicSubscribed",
            "atMs": now_ms(),
            "curves": registry.bonding_curves().len(),
            "refreshMs": refresh_ms,
        }),
    );
    eprintln!(
        "rust-sidecar: dynamic exit curve-account subscription active curves={}",
        registry.bonding_curves().len()
    );

    let stats_interval_ms =
        parse_env_u64("RUST_EXIT_CURVE_ACCOUNT_DYNAMIC_STATS_MS", 30_000).clamp(1_000, 300_000);
    let mut last_stats_at = Instant::now();
    let mut account_updates = 0u64;
    let mut non_account_updates = 0u64;
    loop {
        let next = timeout(
            Duration::from_millis(cfg.exit_subscription_refresh_ms.max(25)),
            updates.message(),
        )
        .await;

        let update = match next {
            Ok(Ok(Some(update))) => update,
            Ok(Ok(None)) => return Ok(()),
            Ok(Err(error)) => return Err(error.into()),
            Err(_) => {
                if last_stats_at.elapsed() >= Duration::from_millis(stats_interval_ms) {
                    emit_selected_candidate_sniper_event(
                        &cfg.selected_candidate_sniper,
                        json!({
                            "kind": "rustExitCurveAccountDynamicStats",
                            "atMs": now_ms(),
                            "reason": "timeout_tick",
                            "curves": registry.bonding_curves().len(),
                            "accountUpdates": account_updates,
                            "nonAccountUpdates": non_account_updates,
                        }),
                    );
                    last_stats_at = Instant::now();
                }
                continue;
            }
        };
        if matches!(update.update_oneof, Some(UpdateOneof::Account(_))) {
            account_updates = account_updates.saturating_add(1);
        } else {
            non_account_updates = non_account_updates.saturating_add(1);
        }
        handle_exit_curve_account_update(update, registry, exit_actions, cfg)?;
        if last_stats_at.elapsed() >= Duration::from_millis(stats_interval_ms) {
            emit_selected_candidate_sniper_event(
                &cfg.selected_candidate_sniper,
                json!({
                    "kind": "rustExitCurveAccountDynamicStats",
                    "atMs": now_ms(),
                    "reason": "stream_update",
                    "curves": registry.bonding_curves().len(),
                    "accountUpdates": account_updates,
                    "nonAccountUpdates": non_account_updates,
                }),
            );
            last_stats_at = Instant::now();
        }
    }
}

fn handle_exit_curve_account_update(
    update: SubscribeUpdate,
    registry: &exit_watcher::ExitWatcherRegistry,
    exit_actions: &UnboundedSender<exit_watcher::ExitAction>,
    cfg: &Config,
) -> Result<()> {
    let Some(UpdateOneof::Account(account_update)) = update.update_oneof else {
        return Ok(());
    };
    let Some(account) = account_update.account else {
        return Ok(());
    };
    let pubkey = bs58::encode(account.pubkey).into_string();
    let (mark, action, fallback) = registry.on_curve_account(
        &pubkey,
        &account.data,
        account_update.slot,
        now_ms().min(u64::MAX as u128) as u64,
    );
    if let Some(mark) = mark {
        emit_json_event(
            cfg.selected_candidate_sniper.audit_file.as_deref(),
            exit_mark_value(&mark, "yellowstone-exit-account"),
        );
    }
    if let Some(fallback) = fallback {
        emit_json_event(
            cfg.selected_candidate_sniper.audit_file.as_deref(),
            serde_json::to_value(&fallback)?,
        );
    }
    if let Some(action) = action {
        let _ = exit_actions.send(action);
    }
    Ok(())
}

async fn run_exit_curve_poll_fallback(
    cfg: &Config,
    registry: &exit_watcher::ExitWatcherRegistry,
    exit_actions: &UnboundedSender<exit_watcher::ExitAction>,
) {
    let rpc_url = cfg.hot_sniper.rpc_fallback_url.trim().to_string();
    if rpc_url.is_empty() {
        eprintln!("rust-sidecar: exit curve RPC poll fallback disabled: rpc fallback URL is empty");
        return;
    }
    let stale_after_ms = parse_env_u64(
        "RUST_EXIT_POLL_FALLBACK_LOCAL_STALE_MS",
        cfg.exit_poll_fallback_ms * 4,
    )
    .clamp(cfg.exit_poll_fallback_ms.max(50), 30_000);
    let error_log_ms = parse_env_u64("RUST_EXIT_POLL_FALLBACK_ERROR_LOG_MS", 2_500)
        .clamp(cfg.exit_poll_fallback_ms.max(50), 60_000);
    let mut last_error_log_by_curve: HashMap<String, u64> = HashMap::new();
    eprintln!(
        "rust-sidecar: exit curve RPC poll fallback stale-only ON staleAfterMs={} errorLogMs={}",
        stale_after_ms, error_log_ms
    );
    loop {
        let now = now_ms().min(u64::MAX as u128) as u64;
        let curves = registry.bonding_curves_needing_poll(stale_after_ms, now);
        if curves.is_empty() {
            sleep(Duration::from_millis(cfg.exit_poll_fallback_ms)).await;
            continue;
        }
        for curve in curves {
            match fetch_account_data_base64(&rpc_url, &curve, cfg.exit_poll_rpc_timeout_ms).await {
                Ok(data) => {
                    handle_exit_curve_account_bytes(
                        &curve,
                        &data,
                        0,
                        "rpc-stale-fallback",
                        registry,
                        exit_actions,
                        cfg,
                    );
                }
                Err(err) => {
                    let now = now_ms().min(u64::MAX as u128) as u64;
                    let last = last_error_log_by_curve.get(&curve).copied().unwrap_or(0);
                    if now.saturating_sub(last) >= error_log_ms {
                        eprintln!(
                            "rust-sidecar: exit curve RPC stale fallback failed for {}...: {err:#}",
                            curve.chars().take(8).collect::<String>()
                        );
                        last_error_log_by_curve.insert(curve.clone(), now);
                    }
                }
            }
        }
        sleep(Duration::from_millis(cfg.exit_poll_fallback_ms)).await;
    }
}

fn handle_exit_curve_account_bytes(
    pubkey: &str,
    data: &[u8],
    slot: u64,
    source: &'static str,
    registry: &exit_watcher::ExitWatcherRegistry,
    exit_actions: &UnboundedSender<exit_watcher::ExitAction>,
    cfg: &Config,
) {
    let (mark, action, fallback) = registry.on_curve_account_with_source(
        pubkey,
        data,
        slot,
        now_ms().min(u64::MAX as u128) as u64,
        source,
    );
    if let Some(mark) = mark {
        emit_json_event(
            cfg.selected_candidate_sniper.audit_file.as_deref(),
            exit_mark_value(&mark, source),
        );
    }
    if let Some(fallback) = fallback {
        emit_json_event(
            cfg.selected_candidate_sniper.audit_file.as_deref(),
            serde_json::to_value(&fallback).unwrap_or_else(|_| json!({})),
        );
    }
    if let Some(action) = action {
        let _ = exit_actions.send(action);
    }
}

fn handle_exit_curve_state(
    pubkey: &str,
    state: exit_watcher::CurveState,
    slot: u64,
    observed_at_ms: u64,
    source: &'static str,
    registry: &exit_watcher::ExitWatcherRegistry,
    exit_actions: &UnboundedSender<exit_watcher::ExitAction>,
    cfg: &Config,
) {
    let (mark, action, fallback) =
        registry.on_curve_state(pubkey, state, slot, observed_at_ms, source);
    if let Some(mark) = mark {
        emit_json_event(
            cfg.selected_candidate_sniper.audit_file.as_deref(),
            exit_mark_value(&mark, source),
        );
    }
    if let Some(fallback) = fallback {
        emit_json_event(
            cfg.selected_candidate_sniper.audit_file.as_deref(),
            serde_json::to_value(&fallback).unwrap_or_else(|_| json!({})),
        );
    }
    if let Some(action) = action {
        let _ = exit_actions.send(action);
    }
}

fn exit_mark_value(mark: &exit_watcher::ExitMark, source: &'static str) -> Value {
    let mut value = serde_json::to_value(mark).unwrap_or_else(|_| json!({}));
    if let Value::Object(ref mut object) = value {
        object.insert("markSource".to_string(), json!(source));
        object.insert("triggerAuthoritySource".to_string(), json!(mark.source));
    }
    value
}

async fn run_selected_candidate_balance_cache_stream(cfg: &Config) -> Result<()> {
    run_selected_candidate_balance_cache_stream_at_endpoint(
        cfg,
        cfg.endpoint
            .as_deref()
            .context("selected candidate balance-cache stream missing gRPC endpoint")?,
        None,
        None,
    )
    .await
}

async fn run_selected_candidate_balance_cache_stream_at_endpoint(
    cfg: &Config,
    endpoint: &str,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let wallet = selected_candidate_wallet_pubkey()
        .context("selected candidate ATA balance stream wallet unavailable")?
        .to_string();
    loop {
        let mut subscribed_accounts = cfg
            .selected_candidate_sniper
            .balance_cache
            .watched_token_accounts();
        let mut request_generation: i32 = 1;

        let token: AsciiMetadataValue = cfg
            .x_token
            .as_ref()
            .context("x-token missing")?
            .parse()
            .context("failed to parse SHYFT_GRPC_X_TOKEN as metadata")?;
        let channel = Channel::from_shared(endpoint.to_string())?
            .tls_config(ClientTlsConfig::new().with_native_roots())?
            .connect()
            .await?;
        let mut client = GeyserClient::with_interceptor(channel, move |mut req: Request<()>| {
            req.metadata_mut().insert("x-token", token.clone());
            Ok::<_, Status>(req)
        })
        .max_decoding_message_size(64 * 1024 * 1024);

        let (request_tx, request_rx) = futures_mpsc::unbounded::<SubscribeRequest>();
        let mut initial_request = build_selected_candidate_balance_cache_request_for_watches(
            &wallet,
            &subscribed_accounts,
        );
        initial_request.ping = Some(SubscribeRequestPing {
            id: request_generation,
        });
        let initial_request_sent_at_ms = now_ms();
        let initial_atas = subscribed_accounts
            .iter()
            .map(|account| account.ata.clone())
            .collect::<Vec<_>>();
        request_tx.unbounded_send(initial_request).map_err(|err| {
            anyhow::anyhow!(
                "selected candidate ATA balance request stream closed before subscribe: {err}"
            )
        })?;
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateBalanceCacheSubscribeRequestSent",
                "atMs": initial_request_sent_at_ms,
                "endpoint": endpoint,
                "generation": request_generation,
                "accounts": subscribed_accounts.len(),
                "atas": initial_atas,
                "addedAtas": initial_atas,
                "removedAtas": [],
                "warmWallet": subscribed_accounts.is_empty(),
                "fullDataDiagnostic": parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_FULL_DATA_DIAGNOSTIC",
                    false
                ),
            }),
        );
        let mut updates = client
            .subscribe(Request::new(request_rx))
            .await?
            .into_inner();
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateBalanceCacheSubscribed",
                "atMs": now_ms(),
                "endpoint": endpoint,
                "accounts": subscribed_accounts.len(),
                "mode": "warm_live_request_updates",
                "warmWallet": subscribed_accounts.is_empty(),
            }),
        );

        loop {
            let next = timeout(
                Duration::from_millis(cfg.exit_subscription_refresh_ms.max(25)),
                updates.message(),
            )
            .await;
            let current_accounts = cfg
                .selected_candidate_sniper
                .balance_cache
                .watched_token_accounts();
            if current_accounts != subscribed_accounts {
                let old_accounts = subscribed_accounts.len();
                let old_atas = subscribed_accounts
                    .iter()
                    .map(|account| account.ata.clone())
                    .collect::<HashSet<_>>();
                let new_atas = current_accounts
                    .iter()
                    .map(|account| account.ata.clone())
                    .collect::<HashSet<_>>();
                let mut added_atas = new_atas.difference(&old_atas).cloned().collect::<Vec<_>>();
                let mut removed_atas = old_atas.difference(&new_atas).cloned().collect::<Vec<_>>();
                added_atas.sort();
                removed_atas.sort();
                request_generation = request_generation.saturating_add(1);
                let mut request = build_selected_candidate_balance_cache_request_for_watches(
                    &wallet,
                    &current_accounts,
                );
                request.ping = Some(SubscribeRequestPing {
                    id: request_generation,
                });
                let request_sent_at_ms = now_ms();
                request_tx.unbounded_send(request).map_err(|err| {
                    anyhow::anyhow!("selected candidate ATA balance request update failed: {err}")
                })?;
                emit_selected_candidate_sniper_event(
                    &cfg.selected_candidate_sniper,
                    json!({
                        "kind": "rustSelectedCandidateBalanceCacheSubscribeRequestUpdated",
                        "atMs": request_sent_at_ms,
                        "endpoint": endpoint,
                        "generation": request_generation,
                        "oldAccounts": old_accounts,
                        "newAccounts": current_accounts.len(),
                        "addedAtas": added_atas,
                        "removedAtas": removed_atas,
                        "warmWallet": current_accounts.is_empty(),
                        "fullDataDiagnostic": parse_env_bool(
                            "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_FULL_DATA_DIAGNOSTIC",
                            false
                        ),
                    }),
                );
                subscribed_accounts = current_accounts;
            }

            let update = match next {
                Ok(Ok(Some(update))) => update,
                Ok(Ok(None)) => return Ok(()),
                Ok(Err(error)) => return Err(error.into()),
                Err(_) => continue,
            };
            handle_selected_candidate_balance_cache_update(
                cfg,
                update,
                endpoint,
                exit_watcher,
                exit_actions,
            )?;
        }
    }
}

async fn run_selected_candidate_wallet_balance_cache_stream(cfg: &Config) -> Result<()> {
    run_selected_candidate_wallet_balance_cache_stream_at_endpoint(
        cfg,
        cfg.endpoint
            .as_deref()
            .context("selected candidate wallet balance-cache stream missing gRPC endpoint")?,
        None,
        None,
    )
    .await
}

async fn run_selected_candidate_wallet_balance_cache_stream_at_endpoint(
    cfg: &Config,
    endpoint: &str,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let wallet = selected_candidate_wallet_pubkey()
        .context("selected candidate wallet owner balance stream wallet unavailable")?
        .to_string();
    loop {
        let token: AsciiMetadataValue = cfg
            .x_token
            .as_ref()
            .context("x-token missing")?
            .parse()
            .context("failed to parse SHYFT_GRPC_X_TOKEN as metadata")?;
        let channel = Channel::from_shared(endpoint.to_string())?
            .tls_config(ClientTlsConfig::new().with_native_roots())?
            .connect()
            .await?;
        let mut client = GeyserClient::with_interceptor(channel, move |mut req: Request<()>| {
            req.metadata_mut().insert("x-token", token.clone());
            Ok::<_, Status>(req)
        })
        .max_decoding_message_size(64 * 1024 * 1024);

        let wallet_for_request = wallet.clone();
        let requests = stream::once(async move {
            build_selected_candidate_wallet_balance_cache_request(&wallet_for_request)
        })
        .chain(stream::pending());
        let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateBalanceCacheWalletOwnerSubscribed",
                "atMs": now_ms(),
                "endpoint": endpoint,
                "wallet": wallet,
                "tokenPrograms": [TOKEN_PROGRAM_ID, TOKEN_2022_PROGRAM_ID],
            }),
        );

        loop {
            let next = timeout(
                Duration::from_millis(cfg.exit_subscription_refresh_ms.max(25)),
                updates.message(),
            )
            .await;
            let update = match next {
                Ok(Ok(Some(update))) => update,
                Ok(Ok(None)) => return Ok(()),
                Ok(Err(error)) => return Err(error.into()),
                Err(_) => continue,
            };
            handle_selected_candidate_balance_cache_update(
                cfg,
                update,
                endpoint,
                exit_watcher,
                exit_actions,
            )?;
        }
    }
}

async fn run_selected_candidate_direct_ata_balance_probe(
    cfg: SelectedCandidateSniperConfig,
    candidate: SelectedCandidate,
    ata: String,
) -> Result<()> {
    let endpoints = selected_candidate_direct_ata_balance_probe_grpc_endpoints();
    if endpoints.is_empty() {
        bail!("selected candidate direct ATA balance probe missing gRPC endpoint");
    }
    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateDirectAtaBalanceProbeRaceStarted",
            "atMs": now_ms(),
            "key": candidate.key,
            "mint": candidate.mint,
            "ata": ata,
            "endpointCount": endpoints.len(),
            "endpoints": endpoints,
        }),
    );
    let mut handles = Vec::new();
    for endpoint in endpoints {
        let probe_cfg = cfg.clone();
        let probe_candidate = candidate.clone();
        let probe_ata = ata.clone();
        handles.push(tokio::spawn(async move {
            run_selected_candidate_direct_ata_balance_probe_at_endpoint(
                probe_cfg,
                probe_candidate,
                probe_ata,
                endpoint,
            )
            .await
        }));
    }
    for handle in handles {
        match handle.await {
            Ok(Ok(())) => {}
            Ok(Err(err)) => {
                eprintln!(
                    "rust-sidecar: selected candidate direct ATA balance probe endpoint failed: {err:#}"
                );
            }
            Err(err) => {
                eprintln!(
                    "rust-sidecar: selected candidate direct ATA balance probe task failed: {err:#}"
                );
            }
        }
    }
    Ok(())
}

fn spawn_selected_candidate_direct_ata_balance_probe(
    cfg: &SelectedCandidateSniperConfig,
    candidate: &SelectedCandidate,
    ata: String,
    timing: &'static str,
) {
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateDirectAtaBalanceProbeSpawned",
            "atMs": now_ms(),
            "key": candidate.key,
            "mint": candidate.mint,
            "ata": ata,
            "timing": timing,
        }),
    );
    let probe_cfg = cfg.clone();
    let probe_candidate = candidate.clone();
    tokio::spawn(async move {
        if let Err(err) =
            run_selected_candidate_direct_ata_balance_probe(probe_cfg, probe_candidate, ata).await
        {
            eprintln!(
                "rust-sidecar: selected candidate direct ATA balance probe failed timing={timing}: {err:#}"
            );
        }
    });
}

fn selected_candidate_direct_ata_balance_probe_grpc_endpoints() -> Vec<String> {
    let mut endpoints = env::var("RUST_SELECTED_CANDIDATE_BALANCE_GRPC_URLS")
        .ok()
        .map(|raw| {
            raw.split(',')
                .map(str::trim)
                .filter(|endpoint| !endpoint.is_empty())
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    if endpoints.is_empty() {
        for key in [
            "RUST_SELECTED_CANDIDATE_BALANCE_GRPC_URL",
            "RUST_SIDECAR_GRPC_URL",
            "SHYFT_RABBITSTREAM_URL",
            "SHYFT_GRPC_URL",
        ] {
            if let Ok(endpoint) = env::var(key) {
                let endpoint = endpoint.trim();
                if !endpoint.is_empty() {
                    endpoints.push(endpoint.to_string());
                    break;
                }
            }
        }
    }
    endpoints.sort();
    endpoints.dedup();
    endpoints
}

async fn run_selected_candidate_direct_ata_balance_probe_at_endpoint(
    cfg: SelectedCandidateSniperConfig,
    candidate: SelectedCandidate,
    ata: String,
    endpoint: String,
) -> Result<()> {
    let x_token = env::var("SHYFT_GRPC_X_TOKEN")
        .or_else(|_| env::var("SHYFT_GRPC_TOKEN"))
        .context("selected candidate direct ATA balance probe missing gRPC token")?;
    let wallet = selected_candidate_wallet_pubkey()
        .context("selected candidate direct ATA balance probe wallet unavailable")?
        .to_string();
    let timeout_ms = cfg
        .exact_balance_proof_timeout_ms
        .max(cfg.early_balance_timeout_ms)
        .max(cfg.balance_cache_wait_ms)
        .clamp(100, 10_000);
    let token: AsciiMetadataValue = x_token
        .parse()
        .context("failed to parse SHYFT_GRPC_X_TOKEN as metadata")?;
    let channel = Channel::from_shared(endpoint.clone())?
        .tls_config(ClientTlsConfig::new().with_native_roots())?
        .connect()
        .await?;
    let mut client = GeyserClient::with_interceptor(channel, move |mut req: Request<()>| {
        req.metadata_mut().insert("x-token", token.clone());
        Ok::<_, Status>(req)
    })
    .max_decoding_message_size(64 * 1024 * 1024);

    let request =
        build_selected_candidate_direct_ata_balance_probe_request(&wallet, &candidate, &ata);
    let requests = stream::once(async move { request }).chain(stream::pending());
    let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
    let started = Instant::now();
    emit_selected_candidate_sniper_event(
        &cfg,
        json!({
            "kind": "rustSelectedCandidateDirectAtaBalanceProbeSubscribed",
            "atMs": now_ms(),
            "key": candidate.key,
            "mint": candidate.mint,
            "ata": ata,
            "endpoint": endpoint,
            "tokenProgramId": candidate.token_program_id,
            "timeoutMs": timeout_ms,
            "source": "yellowstone-direct-ata",
        }),
    );

    loop {
        if let Some(cached) = cfg
            .balance_cache
            .get_fresh_exact_for_candidate(&candidate, cfg.balance_cache_max_age_ms)
        {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateDirectAtaBalanceProbeExactProof",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "ata": cached.ata,
                    "probeEndpoint": endpoint,
                    "slot": cached.slot,
                    "observedAtMs": cached.observed_at_ms,
                    "amountRaw": cached.amount_raw,
                    "exact": cached.exact,
                    "balanceSource": cached.source,
                    "elapsedMs": started.elapsed().as_millis(),
                }),
            );
            return Ok(());
        }
        let elapsed_ms = started.elapsed().as_millis() as u64;
        if elapsed_ms >= timeout_ms {
            emit_selected_candidate_sniper_event(
                &cfg,
                json!({
                    "kind": "rustSelectedCandidateDirectAtaBalanceProbeTimeout",
                    "atMs": now_ms(),
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "ata": ata,
                    "endpoint": endpoint,
                    "timeoutMs": timeout_ms,
                    "elapsedMs": elapsed_ms,
                }),
            );
            return Ok(());
        }
        let next = timeout(
            Duration::from_millis(timeout_ms.saturating_sub(elapsed_ms).min(25).max(1)),
            updates.message(),
        )
        .await;
        match next {
            Ok(Ok(Some(update))) => {
                handle_selected_candidate_direct_ata_balance_probe_update(&cfg, update, &endpoint)?
            }
            Ok(Ok(None)) => return Ok(()),
            Ok(Err(error)) => return Err(error.into()),
            Err(_) => {}
        }
    }
}

fn handle_selected_candidate_direct_ata_balance_probe_update(
    cfg: &SelectedCandidateSniperConfig,
    update: SubscribeUpdate,
    endpoint: &str,
) -> Result<()> {
    let filters = update.filters.clone();
    let created_at = yellowstone_update_created_at_json(update.created_at.as_ref());
    let Some(update_oneof) = update.update_oneof else {
        return Ok(());
    };
    let UpdateOneof::Account(account_update) = update_oneof else {
        if let UpdateOneof::Transaction(tx_update) = update_oneof {
            handle_selected_candidate_balance_cache_transaction_update_for_selected_cfg(
                cfg, tx_update, filters, created_at, endpoint, None, None,
            )?;
        }
        return Ok(());
    };
    let Some(account) = account_update.account else {
        return Ok(());
    };
    let ata = bs58::encode(account.pubkey).into_string();
    let account_owner = bs58::encode(account.owner).into_string();
    let txn_signature = account
        .txn_signature
        .as_ref()
        .map(|signature| bs58::encode(signature).into_string());
    let decoded_full = decode_spl_token_account_balance(&account.data);
    let decoded_slice = if decoded_full.is_none() {
        decode_spl_token_account_owner_amount_slice(&account.data)
    } else {
        None
    };
    let Some((mint, owner, amount_raw, data_layout)) = decoded_full
        .map(|(mint, owner, amount_raw)| (Some(mint), owner, amount_raw, "full"))
        .or_else(|| {
            decoded_slice.map(|(owner, amount_raw)| (None, owner, amount_raw, "owner_amount_slice"))
        })
    else {
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateDirectAtaBalanceProbeAccountSeen",
                "atMs": now_ms(),
                "ata": ata,
                "endpoint": endpoint,
                "accountOwner": account_owner,
                "slot": account_update.slot,
                "writeVersion": account.write_version,
                "txnSignature": txn_signature,
                "filters": filters,
                "serverCreatedAt": created_at,
                "decoded": false,
                "cached": false,
                "dataLen": account.data.len(),
            }),
        );
        return Ok(());
    };
    let observed_at_ms = now_ms().min(u64::MAX as u128) as u64;
    let seen_mint = mint.clone();
    let seen_amount_raw = amount_raw;
    let cached = if let Some(mint) = mint {
        if account_owner != TOKEN_PROGRAM_ID && account_owner != TOKEN_2022_PROGRAM_ID {
            return Ok(());
        }
        cfg.balance_cache.update_wallet_owned(
            ata.clone(),
            mint,
            owner,
            account_owner.clone(),
            amount_raw,
            account_update.slot,
            observed_at_ms,
        )
    } else {
        cfg.balance_cache.update_watched_owner_amount_slice(
            ata.clone(),
            account_owner.clone(),
            owner,
            amount_raw,
            account_update.slot,
            observed_at_ms,
        )
    };
    let cached_ok = cached.is_some();
    let seen_mint = seen_mint.or_else(|| cached.as_ref().map(|cached| cached.mint.clone()));
    if let Some(cached) = cached.as_ref() {
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateDirectAtaBalanceProbeCacheUpdate",
                "atMs": now_ms(),
                "ata": cached.ata,
                "endpoint": endpoint,
                "mint": cached.mint,
                "tokenProgramId": cached.token_program_id,
                "amountRaw": cached.amount_raw,
                "slot": cached.slot,
                "writeVersion": account.write_version,
                "txnSignature": txn_signature,
                "filters": filters,
                "serverCreatedAt": created_at,
                "observedAtMs": cached.observed_at_ms,
                "exact": cached.exact,
                "balanceSource": cached.source,
                "dataLayout": data_layout,
            }),
        );
    }
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateDirectAtaBalanceProbeAccountSeen",
            "atMs": now_ms(),
            "ata": ata,
            "endpoint": endpoint,
            "mint": seen_mint,
            "accountOwner": account_owner,
            "slot": account_update.slot,
            "writeVersion": account.write_version,
            "txnSignature": txn_signature,
            "filters": filters,
            "serverCreatedAt": created_at,
            "decoded": true,
            "cached": cached_ok,
            "amountRaw": seen_amount_raw,
            "dataLen": account.data.len(),
            "dataLayout": data_layout,
        }),
    );
    Ok(())
}

fn handle_selected_candidate_balance_cache_update(
    cfg: &Config,
    update: SubscribeUpdate,
    endpoint: &str,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let filters = update.filters.clone();
    let created_at = yellowstone_update_created_at_json(update.created_at.as_ref());
    let Some(update_oneof) = update.update_oneof else {
        return Ok(());
    };
    match update_oneof {
        UpdateOneof::Account(account_update) => {
            handle_selected_candidate_balance_cache_account_update(
                cfg,
                account_update,
                filters,
                created_at,
                endpoint,
                exit_watcher,
                exit_actions,
            )
        }
        UpdateOneof::Transaction(tx_update) => {
            handle_selected_candidate_balance_cache_transaction_update(
                cfg,
                tx_update,
                filters,
                created_at,
                endpoint,
                exit_watcher,
                exit_actions,
            )
        }
        UpdateOneof::Pong(pong) => {
            emit_selected_candidate_sniper_event(
                &cfg.selected_candidate_sniper,
                json!({
                    "kind": "rustSelectedCandidateBalanceCachePong",
                    "atMs": now_ms(),
                    "endpoint": endpoint,
                    "id": pong.id,
                    "filters": filters,
                    "serverCreatedAt": created_at,
                    "diagnosticOnly": true,
                }),
            );
            Ok(())
        }
        _ => Ok(()),
    }
}

fn handle_selected_candidate_balance_cache_account_update(
    cfg: &Config,
    account_update: yellowstone_grpc_proto::geyser::SubscribeUpdateAccount,
    filters: Vec<String>,
    created_at: Option<Value>,
    endpoint: &str,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let Some(account) = account_update.account else {
        return Ok(());
    };
    let ata = bs58::encode(account.pubkey).into_string();
    let account_owner = bs58::encode(account.owner).into_string();
    let txn_signature = account
        .txn_signature
        .as_ref()
        .map(|signature| bs58::encode(signature).into_string());
    let decoded_full = decode_spl_token_account_balance(&account.data);
    let decoded_slice = if decoded_full.is_none() {
        decode_spl_token_account_owner_amount_slice(&account.data)
    } else {
        None
    };
    let Some((mint, owner, amount_raw, data_layout)) = decoded_full
        .map(|(mint, owner, amount_raw)| (Some(mint), owner, amount_raw, "full"))
        .or_else(|| {
            decoded_slice.map(|(owner, amount_raw)| (None, owner, amount_raw, "owner_amount_slice"))
        })
    else {
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateBalanceCacheAccountSeen",
                "atMs": now_ms(),
                "endpoint": endpoint,
                "ata": ata,
                "accountOwner": account_owner,
                "slot": account_update.slot,
                "writeVersion": account.write_version,
                "txnSignature": txn_signature,
                "filters": filters,
                "serverCreatedAt": created_at,
                "decoded": false,
                "cached": false,
                "dataLen": account.data.len(),
            }),
        );
        return Ok(());
    };
    let observed_at_ms = now_ms().min(u64::MAX as u128) as u64;
    let seen_ata = ata.clone();
    let seen_mint = mint.clone();
    let seen_account_owner = account_owner.clone();
    let seen_amount_raw = amount_raw;
    let cached = if let Some(mint) = mint {
        if account_owner != TOKEN_PROGRAM_ID && account_owner != TOKEN_2022_PROGRAM_ID {
            return Ok(());
        }
        cfg.selected_candidate_sniper
            .balance_cache
            .update_wallet_owned(
                ata,
                mint,
                owner,
                account_owner,
                amount_raw,
                account_update.slot,
                observed_at_ms,
            )
    } else {
        cfg.selected_candidate_sniper
            .balance_cache
            .update_watched_owner_amount_slice(
                ata,
                account_owner,
                owner,
                amount_raw,
                account_update.slot,
                observed_at_ms,
            )
    };
    let cached_ok = cached.is_some();
    let seen_mint = seen_mint.or_else(|| cached.as_ref().map(|cached| cached.mint.clone()));
    if let Some(cached) = cached.as_ref() {
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateBalanceCacheUpdate",
                "atMs": now_ms(),
                "endpoint": endpoint,
                "ata": cached.ata,
                "mint": cached.mint,
                "tokenProgramId": cached.token_program_id,
                "amountRaw": cached.amount_raw,
                "slot": cached.slot,
                "writeVersion": account.write_version,
                "txnSignature": txn_signature,
                "filters": filters,
                "serverCreatedAt": created_at,
                "observedAtMs": cached.observed_at_ms,
                "exact": cached.exact,
                "balanceSource": cached.source,
                "proofClass": if cached.exact {
                    "exact_token_account_balance"
                } else {
                    "local_submitted_buy_signature"
                },
                "reconciliationRequired": !cached.exact,
                "dataLayout": data_layout,
            }),
        );
        maybe_activate_selected_candidate_stream_exact_balance(
            &cfg.selected_candidate_sniper,
            exit_watcher,
            exit_actions,
            cached,
            "balance-cache-account-update",
        );
    }
    emit_selected_candidate_sniper_event(
        &cfg.selected_candidate_sniper,
        json!({
            "kind": "rustSelectedCandidateBalanceCacheAccountSeen",
            "atMs": now_ms(),
            "endpoint": endpoint,
            "ata": seen_ata,
            "mint": seen_mint,
            "accountOwner": seen_account_owner,
            "slot": account_update.slot,
            "writeVersion": account.write_version,
            "txnSignature": txn_signature,
            "filters": filters,
            "serverCreatedAt": created_at,
            "decoded": true,
            "cached": cached_ok,
            "amountRaw": seen_amount_raw,
            "dataLen": account.data.len(),
            "dataLayout": data_layout,
        }),
    );
    Ok(())
}

fn handle_selected_candidate_balance_cache_transaction_update(
    cfg: &Config,
    tx_update: yellowstone_grpc_proto::geyser::SubscribeUpdateTransaction,
    filters: Vec<String>,
    created_at: Option<Value>,
    endpoint: &str,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    handle_selected_candidate_balance_cache_transaction_update_for_selected_cfg(
        &cfg.selected_candidate_sniper,
        tx_update,
        filters,
        created_at,
        endpoint,
        exit_watcher,
        exit_actions,
    )
}

fn handle_selected_candidate_balance_cache_transaction_update_for_selected_cfg(
    cfg: &SelectedCandidateSniperConfig,
    tx_update: yellowstone_grpc_proto::geyser::SubscribeUpdateTransaction,
    filters: Vec<String>,
    created_at: Option<Value>,
    endpoint: &str,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    if !cfg.balance_cache_wallet_owner_stream_enabled {
        return Ok(());
    }
    let Some(info) = tx_update.transaction else {
        return Ok(());
    };
    let Some(meta) = info.meta else {
        return Ok(());
    };
    let expected_owner = selected_candidate_wallet_pubkey()
        .context("selected candidate wallet owner unavailable for transaction balance cache")?
        .to_string();
    let signature = bs58::encode(info.signature).into_string();
    let observed_at_ms = now_ms().min(u64::MAX as u128) as u64;
    let transaction_error = meta.err.is_some();
    let post_token_balance_count = meta.post_token_balances.len();
    let mut owner_mismatch = 0_u64;
    let mut program_mismatch = 0_u64;
    let mut amount_parse_fail = 0_u64;
    let mut ata_derive_fail = 0_u64;
    let mut cached_count = 0_u64;
    let mut submitted_buy_proof_cached = false;
    let mut submitted_buy_post_token_exact_cached = false;
    let mut submitted_buy_proof_discarded = false;
    let submitted_buy_proof = if transaction_error {
        submitted_buy_proof_discarded = cfg
            .balance_cache
            .discard_submitted_buy_signature(&signature);
        None
    } else {
        cfg.balance_cache.commit_submitted_buy_landed_signature(
            &signature,
            tx_update.slot,
            observed_at_ms,
        )
    };
    if let Some(cached) = submitted_buy_proof.as_ref() {
        submitted_buy_proof_cached = true;
        cached_count = cached_count.saturating_add(1);
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateBalanceCacheSubmittedBuyProofUpdate",
                "atMs": now_ms(),
                "endpoint": endpoint,
                "signature": signature,
                "filters": filters,
                "serverCreatedAt": created_at,
                "ata": cached.ata,
                "mint": cached.mint,
                "tokenProgramId": cached.token_program_id,
                "amountRaw": cached.amount_raw,
                "slot": cached.slot,
                "observedAtMs": cached.observed_at_ms,
                "exact": cached.exact,
                "balanceSource": cached.source,
            }),
        );
    }
    for balance in meta.post_token_balances {
        if balance.owner != expected_owner {
            owner_mismatch = owner_mismatch.saturating_add(1);
            continue;
        }
        if balance.program_id != TOKEN_PROGRAM_ID && balance.program_id != TOKEN_2022_PROGRAM_ID {
            program_mismatch = program_mismatch.saturating_add(1);
            continue;
        }
        let Some(amount_raw) = balance
            .ui_token_amount
            .as_ref()
            .and_then(|amount| amount.amount.parse::<u64>().ok())
        else {
            amount_parse_fail = amount_parse_fail.saturating_add(1);
            continue;
        };
        let Ok((ata, token_program_id)) =
            selected_candidate_ata_for_mint(&balance.mint, Some(&balance.program_id))
        else {
            ata_derive_fail = ata_derive_fail.saturating_add(1);
            continue;
        };
        if let Some(cached) = cfg.balance_cache.commit_submitted_buy_post_token_balance(
            &signature,
            &balance.mint,
            &expected_owner,
            &token_program_id,
            amount_raw,
            tx_update.slot,
            observed_at_ms,
        ) {
            submitted_buy_post_token_exact_cached = true;
            cached_count = cached_count.saturating_add(1);
            emit_selected_candidate_sniper_event(
                cfg,
                json!({
                    "kind": "rustSelectedCandidateBalanceCacheSubmittedBuyExactProofUpdate",
                    "atMs": now_ms(),
                    "endpoint": endpoint,
                    "signature": signature,
                    "accountIndex": balance.account_index,
                    "filters": filters,
                    "serverCreatedAt": created_at,
                    "ata": cached.ata,
                    "mint": cached.mint,
                    "tokenProgramId": cached.token_program_id,
                    "amountRaw": cached.amount_raw,
                    "slot": cached.slot,
                    "observedAtMs": cached.observed_at_ms,
                    "exact": cached.exact,
                    "balanceSource": cached.source,
                    "proofClass": "submitted_buy_post_token_balance",
                }),
            );
            maybe_activate_selected_candidate_stream_exact_balance(
                cfg,
                exit_watcher,
                exit_actions,
                &cached,
                "balance-cache-submitted-buy-post-token",
            );
            continue;
        }
        let Some(cached) = cfg.balance_cache.update_wallet_owned(
            ata,
            balance.mint,
            expected_owner.clone(),
            token_program_id,
            amount_raw,
            tx_update.slot,
            observed_at_ms,
        ) else {
            continue;
        };
        cached_count = cached_count.saturating_add(1);
        emit_selected_candidate_sniper_event(
            cfg,
            json!({
                "kind": "rustSelectedCandidateBalanceCacheTransactionUpdate",
                "atMs": now_ms(),
                "endpoint": endpoint,
                "signature": signature,
                "accountIndex": balance.account_index,
                "filters": filters,
                "serverCreatedAt": created_at,
                "ata": cached.ata,
                "mint": cached.mint,
                "tokenProgramId": cached.token_program_id,
                "amountRaw": cached.amount_raw,
                "slot": cached.slot,
                "observedAtMs": cached.observed_at_ms,
                "exact": cached.exact,
                "balanceSource": cached.source,
            }),
        );
        maybe_activate_selected_candidate_stream_exact_balance(
            cfg,
            exit_watcher,
            exit_actions,
            &cached,
            "balance-cache-transaction-wallet-owned",
        );
    }
    emit_selected_candidate_sniper_event(
        cfg,
        json!({
            "kind": "rustSelectedCandidateBalanceCacheTransactionSeen",
            "atMs": now_ms(),
            "endpoint": endpoint,
            "signature": signature,
            "slot": tx_update.slot,
            "filters": filters,
            "serverCreatedAt": created_at,
            "postTokenBalances": post_token_balance_count,
            "cachedBalances": cached_count,
            "submittedBuyProofCached": submitted_buy_proof_cached,
            "submittedBuyPostTokenExactCached": submitted_buy_post_token_exact_cached,
            "submittedBuyProofDiscarded": submitted_buy_proof_discarded,
            "transactionError": transaction_error,
            "ownerMismatch": owner_mismatch,
            "programMismatch": program_mismatch,
            "amountParseFail": amount_parse_fail,
            "ataDeriveFail": ata_derive_fail,
        }),
    );
    Ok(())
}

fn yellowstone_update_created_at_json(
    created_at: Option<&yellowstone_grpc_proto::prost_types::Timestamp>,
) -> Option<Value> {
    created_at.map(|timestamp| {
        json!({
            "seconds": timestamp.seconds,
            "nanos": timestamp.nanos,
        })
    })
}

async fn run_selected_candidate_curve_cache_stream(
    cfg: &Config,
    endpoint: &str,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    loop {
        let owner_wide = cfg.selected_candidate_sniper.curve_cache_owner_wide;
        let curves = cfg.selected_candidate_curve_cache.watched_curves();
        if curves.is_empty() && !owner_wide {
            sleep(Duration::from_millis(
                cfg.exit_subscription_refresh_ms.max(25),
            ))
            .await;
            continue;
        }

        let token: AsciiMetadataValue = cfg
            .x_token
            .as_ref()
            .context("x-token missing")?
            .parse()
            .context("failed to parse SHYFT_GRPC_X_TOKEN as metadata")?;
        let channel = Channel::from_shared(endpoint.to_string())?
            .tls_config(ClientTlsConfig::new().with_native_roots())?
            .connect()
            .await?;
        let mut client = GeyserClient::with_interceptor(channel, move |mut req: Request<()>| {
            req.metadata_mut().insert("x-token", token.clone());
            Ok::<_, Status>(req)
        })
        .max_decoding_message_size(64 * 1024 * 1024);

        let requested_curves = curves.clone();
        let owner_filter = owner_wide.then(|| PUMP_FUN_PROGRAM_ID.to_string());
        let tracked_wallets = cfg.tracked_wallets.clone();
        let (request_tx, request_rx) = mpsc::unbounded_channel::<SubscribeRequest>();
        request_tx
            .send(build_selected_candidate_curve_cache_request(
                &tracked_wallets,
                &requested_curves,
                owner_wide,
            ))
            .context("selected candidate curve cache initial request send failed")?;
        let refresh_request_tx = request_tx.clone();
        let tracked_wallets = cfg.tracked_wallets.clone();
        let curve_cache = cfg.selected_candidate_curve_cache.clone();
        let sniper_cfg = cfg.selected_candidate_sniper.clone();
        let refresh_ms = cfg.exit_subscription_refresh_ms.max(25);
        tokio::spawn(async move {
            let mut last_curves = requested_curves;
            loop {
                sleep(Duration::from_millis(refresh_ms)).await;
                let current_curves = curve_cache.watched_curves();
                if current_curves == last_curves {
                    continue;
                }
                if refresh_request_tx
                    .send(build_selected_candidate_curve_cache_request(
                        &tracked_wallets,
                        &current_curves,
                        owner_wide,
                    ))
                    .is_err()
                {
                    break;
                }
                emit_selected_candidate_sniper_event(
                    &sniper_cfg,
                    json!({
                        "kind": "rustSelectedCandidateCurveCacheRefresh",
                        "atMs": now_ms(),
                        "oldCurves": last_curves.len(),
                        "newCurves": current_curves.len(),
                        "ownerWide": owner_wide,
                        "mode": "stream_update",
                    }),
                );
                last_curves = current_curves;
            }
        });
        let _request_tx_keepalive = request_tx;
        let requests = stream::unfold(request_rx, |mut rx| async {
            rx.recv().await.map(|req| (req, rx))
        });
        let mut updates = client.subscribe(Request::new(requests)).await?.into_inner();
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateCurveCacheSubscribed",
                "atMs": now_ms(),
                "endpoint": endpoint,
                "curves": curves.len(),
                "ownerWide": owner_wide,
                "ownerFilter": owner_filter,
                "cacheStates": cfg.selected_candidate_curve_cache.state_count(),
                "yellowstoneCacheStates": cfg.selected_candidate_curve_cache.yellowstone_state_count(),
                "cacheMaxStates": cfg.selected_candidate_curve_cache.max_states,
                "accountUpdates": cfg.selected_candidate_curve_cache.account_update_count(),
                "decodeSuccesses": cfg.selected_candidate_curve_cache.decode_success_count(),
                "decodeSkips": cfg.selected_candidate_curve_cache.decode_skip_count(),
                "yellowstoneAccountUpdates": cfg.selected_candidate_curve_cache.yellowstone_account_update_count(),
                "yellowstoneDecodeSuccesses": cfg.selected_candidate_curve_cache.yellowstone_decode_success_count(),
                "yellowstoneDecodeSkips": cfg.selected_candidate_curve_cache.yellowstone_decode_skip_count(),
            }),
        );

        let stats_interval_ms =
            parse_env_u64("RUST_SELECTED_CANDIDATE_CURVE_CACHE_STATS_MS", 30_000)
                .clamp(1_000, 300_000);
        let mut last_stats_at = Instant::now();
        loop {
            let next = timeout(
                Duration::from_millis(cfg.exit_subscription_refresh_ms.max(25)),
                updates.message(),
            )
            .await;

            let update = match next {
                Ok(Ok(Some(update))) => update,
                Ok(Ok(None)) => return Ok(()),
                Ok(Err(error)) => return Err(error.into()),
                Err(_) => {
                    if last_stats_at.elapsed() >= Duration::from_millis(stats_interval_ms) {
                        emit_selected_candidate_curve_cache_stats(
                            cfg,
                            "timeout_tick",
                            Some(endpoint),
                        );
                        last_stats_at = Instant::now();
                    }
                    continue;
                }
            };
            handle_selected_candidate_curve_cache_update(cfg, update, exit_watcher, exit_actions)?;
            if last_stats_at.elapsed() >= Duration::from_millis(stats_interval_ms) {
                emit_selected_candidate_curve_cache_stats(cfg, "stream_update", Some(endpoint));
                last_stats_at = Instant::now();
            }
        }
    }
}

fn emit_selected_candidate_curve_cache_stats(cfg: &Config, reason: &str, endpoint: Option<&str>) {
    emit_selected_candidate_sniper_event(
        &cfg.selected_candidate_sniper,
        json!({
            "kind": "rustSelectedCandidateCurveCacheStats",
            "atMs": now_ms(),
            "reason": reason,
            "endpoint": endpoint,
            "ownerWide": cfg.selected_candidate_sniper.curve_cache_owner_wide,
            "cacheStates": cfg.selected_candidate_curve_cache.state_count(),
            "yellowstoneCacheStates": cfg.selected_candidate_curve_cache.yellowstone_state_count(),
            "cacheMaxStates": cfg.selected_candidate_curve_cache.max_states,
            "accountUpdates": cfg.selected_candidate_curve_cache.account_update_count(),
            "decodeSuccesses": cfg.selected_candidate_curve_cache.decode_success_count(),
            "decodeSkips": cfg.selected_candidate_curve_cache.decode_skip_count(),
            "yellowstoneAccountUpdates": cfg.selected_candidate_curve_cache.yellowstone_account_update_count(),
            "yellowstoneDecodeSuccesses": cfg.selected_candidate_curve_cache.yellowstone_decode_success_count(),
            "yellowstoneDecodeSkips": cfg.selected_candidate_curve_cache.yellowstone_decode_skip_count(),
        }),
    );
}

async fn run_selected_candidate_reserve_observation_file_cache(
    cfg: &Config,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) {
    let Some(source) = cfg
        .selected_candidate_sniper
        .reserve_observation_source
        .clone()
    else {
        return;
    };
    let mut offset = if cfg
        .selected_candidate_sniper
        .reserve_observation_start_at_end
    {
        fs::metadata(&source).map(|meta| meta.len()).unwrap_or(0)
    } else {
        0
    };
    let mut carry = String::new();
    let poll_ms = cfg
        .selected_candidate_sniper
        .reserve_observation_poll_ms
        .clamp(5, 1_000);
    let stats_interval_ms = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_RESERVE_OBSERVATION_STATS_MS",
        30_000,
    )
    .clamp(1_000, 300_000);
    let mut last_stats_at = Instant::now();
    let mut lines_seen = 0u64;
    let mut updates = 0u64;
    let mut skips = 0u64;
    emit_selected_candidate_sniper_event(
        &cfg.selected_candidate_sniper,
        json!({
            "kind": "rustSelectedCandidateReserveObservationFileCacheStarted",
            "atMs": now_ms(),
            "source": source.clone(),
            "startAtEnd": cfg.selected_candidate_sniper.reserve_observation_start_at_end,
            "pollMs": poll_ms,
            "offset": offset,
        }),
    );
    loop {
        match fs::File::open(&source) {
            Ok(mut file) => {
                let len = file.metadata().map(|meta| meta.len()).unwrap_or(0);
                if len < offset {
                    offset = 0;
                    carry.clear();
                }
                if len > offset && file.seek(SeekFrom::Start(offset)).is_ok() {
                    let mut bytes = Vec::new();
                    if file.read_to_end(&mut bytes).is_err() {
                        cfg.selected_candidate_curve_cache.mark_decode_skip();
                        skips = skips.saturating_add(1);
                    } else if !bytes.is_empty() {
                        offset = len;
                        let chunk = String::from_utf8_lossy(&bytes).to_string();
                        carry.push_str(&chunk);
                        let mut lines = carry.split('\n').map(str::to_string).collect::<Vec<_>>();
                        carry = lines.pop().unwrap_or_default();
                        for line in lines {
                            let trimmed = line.trim();
                            if trimmed.is_empty() {
                                continue;
                            }
                            lines_seen = lines_seen.saturating_add(1);
                            match ingest_selected_candidate_reserve_observation_line(
                                cfg,
                                trimmed,
                                exit_watcher,
                                exit_actions,
                            ) {
                                Ok(true) => updates = updates.saturating_add(1),
                                Ok(false) => {}
                                Err(_) => {
                                    skips = skips.saturating_add(1);
                                    cfg.selected_candidate_curve_cache.mark_decode_skip();
                                }
                            }
                        }
                    }
                }
            }
            Err(err) => {
                emit_selected_candidate_sniper_event(
                    &cfg.selected_candidate_sniper,
                    json!({
                        "kind": "rustSelectedCandidateReserveObservationFileCacheError",
                        "atMs": now_ms(),
                        "source": source.clone(),
                        "error": err.to_string(),
                    }),
                );
            }
        }
        if last_stats_at.elapsed() >= Duration::from_millis(stats_interval_ms) {
            emit_selected_candidate_sniper_event(
                &cfg.selected_candidate_sniper,
                json!({
                    "kind": "rustSelectedCandidateReserveObservationFileCacheStats",
                    "atMs": now_ms(),
                    "source": source.clone(),
                    "offset": offset,
                    "linesSeen": lines_seen,
                    "updates": updates,
                    "skips": skips,
                    "carryBytes": carry.len(),
                    "cacheStates": cfg.selected_candidate_curve_cache.state_count(),
                    "yellowstoneCacheStates": cfg.selected_candidate_curve_cache.yellowstone_state_count(),
                    "cacheMaxStates": cfg.selected_candidate_curve_cache.max_states,
                    "accountUpdates": cfg.selected_candidate_curve_cache.account_update_count(),
                    "decodeSuccesses": cfg.selected_candidate_curve_cache.decode_success_count(),
                    "decodeSkips": cfg.selected_candidate_curve_cache.decode_skip_count(),
                    "yellowstoneAccountUpdates": cfg.selected_candidate_curve_cache.yellowstone_account_update_count(),
                    "yellowstoneDecodeSuccesses": cfg.selected_candidate_curve_cache.yellowstone_decode_success_count(),
                    "yellowstoneDecodeSkips": cfg.selected_candidate_curve_cache.yellowstone_decode_skip_count(),
                }),
            );
            last_stats_at = Instant::now();
        }
        sleep(Duration::from_millis(poll_ms)).await;
    }
}

fn ingest_selected_candidate_reserve_observation_line(
    cfg: &Config,
    line: &str,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<bool> {
    let row =
        serde_json::from_str::<Value>(line).context("reserve observation json parse failed")?;

    let parsed = if json_string_at(&row, &["event"]).as_deref() == Some("reserve_observation") {
        let Some(bonding_curve) = json_string_at(&row, &["bondingCurve"]) else {
            cfg.selected_candidate_curve_cache.mark_decode_skip();
            return Ok(false);
        };
        let Some(virtual_sol_reserves) = json_u64_at(&row, &["virtualSolReserves"]) else {
            cfg.selected_candidate_curve_cache.mark_decode_skip();
            return Ok(false);
        };
        let Some(virtual_token_reserves) = json_u64_at(&row, &["virtualTokenReserves"]) else {
            cfg.selected_candidate_curve_cache.mark_decode_skip();
            return Ok(false);
        };
        let slot = json_u64_at(&row, &["slot"]).unwrap_or(0);
        let observed_at_ms = json_u64_at(&row, &["observedAtMs"])
            .unwrap_or_else(|| now_ms().min(u64::MAX as u128) as u64);
        Some((
            bonding_curve,
            virtual_sol_reserves,
            virtual_token_reserves,
            slot,
            observed_at_ms,
        ))
    } else {
        parse_selected_candidate_decision_input_reserve_observation(&row)
    };

    let Some((bonding_curve, virtual_sol_reserves, virtual_token_reserves, slot, observed_at_ms)) =
        parsed
    else {
        cfg.selected_candidate_curve_cache.mark_decode_skip();
        return Ok(false);
    };
    cfg.selected_candidate_curve_cache.mark_account_update();
    cfg.selected_candidate_curve_cache.mark_decode_success();
    cfg.selected_candidate_curve_cache.update_from_source(
        bonding_curve.clone(),
        CurveState {
            virtual_token_reserves,
            virtual_sol_reserves,
            token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
            complete: false,
            creator: None,
        },
        slot,
        observed_at_ms,
        "reserve-observation-file",
    );
    if let (Some(registry), Some(actions)) = (exit_watcher, exit_actions) {
        let active_exit_curve = registry
            .bonding_curves()
            .iter()
            .any(|curve| curve == &bonding_curve);
        if active_exit_curve {
            emit_selected_candidate_sniper_event(
                &cfg.selected_candidate_sniper,
                json!({
                    "kind": "rustSelectedCandidateReserveObservationExitBridge",
                    "atMs": now_ms(),
                    "bondingCurve": bonding_curve,
                    "slot": slot,
                    "observedAtMs": observed_at_ms,
                    "source": "reserve-observation-file",
                }),
            );
            handle_exit_curve_state(
                &bonding_curve,
                exit_watcher::CurveState {
                    virtual_token_reserves,
                    virtual_sol_reserves,
                    complete: false,
                },
                slot,
                observed_at_ms,
                "reserve-observation-file",
                registry,
                actions,
                cfg,
            );
        }
    }
    Ok(true)
}

fn parse_selected_candidate_decision_input_reserve_observation(
    row: &Value,
) -> Option<(String, u64, u64, u64, u64)> {
    if json_string_at(row, &["schema_version"]).as_deref() != Some("teacher-impulse-parity-v1") {
        return None;
    }
    let bonding_curve = json_string_at(row, &["extra_decision_fields", "bondingCurve"])
        .or_else(|| json_string_at(row, &["raw_record", "bondingCurve"]))
        .or_else(|| json_string_at(row, &["extra", "bondingCurve"]))?;
    let state_identity = json_string_at(row, &["canonical_state", "state_identity"])
        .or_else(|| json_string_at(row, &["canonical_state_identity"]))
        .or_else(|| json_string_at(row, &["stateIdentity"]))?;
    let (virtual_sol_reserves, virtual_token_reserves) =
        parse_native_shredstream_replay_reserves(&state_identity)?;
    let slot = json_u64_at(row, &["canonical_state", "source_slot"])
        .or_else(|| json_u64_at(row, &["provider", "source_slot"]))
        .or_else(|| json_u64_at(row, &["source_slot"]))
        .unwrap_or(0);
    let observed_at_ms = json_u64_at(row, &["canonical_state", "observed_at_ms"])
        .or_else(|| json_u64_at(row, &["provider", "candidate_ready_ts_ms"]))
        .or_else(|| json_u64_at(row, &["provider", "decode_complete_ts_ms"]))
        .or_else(|| json_u64_at(row, &["local_receive_ts_ms"]))
        .unwrap_or_else(|| now_ms().min(u64::MAX as u128) as u64);
    Some((
        bonding_curve,
        virtual_sol_reserves,
        virtual_token_reserves,
        slot,
        observed_at_ms,
    ))
}

fn parse_native_shredstream_replay_reserves(state_identity: &str) -> Option<(u64, u64)> {
    let mut parts = state_identity.split(':').collect::<Vec<_>>();
    if parts.len() < 4 || parts.first().copied() != Some("native-shredstream-replay") {
        return None;
    }
    let virtual_token_reserves = parts.pop()?.parse::<u64>().ok()?;
    let virtual_sol_reserves = parts.pop()?.parse::<u64>().ok()?;
    Some((virtual_sol_reserves, virtual_token_reserves))
}

fn handle_selected_candidate_curve_cache_update(
    cfg: &Config,
    update: SubscribeUpdate,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let Some(UpdateOneof::Account(account_update)) = update.update_oneof else {
        return Ok(());
    };
    let Some(account) = account_update.account else {
        return Ok(());
    };
    cfg.selected_candidate_curve_cache.mark_account_update();
    cfg.selected_candidate_curve_cache
        .mark_yellowstone_account_update();
    let bonding_curve = bs58::encode(account.pubkey).into_string();
    let Some(state) = decode_bonding_curve_state(&account.data) else {
        cfg.selected_candidate_curve_cache.mark_decode_skip();
        cfg.selected_candidate_curve_cache
            .mark_yellowstone_decode_skip();
        return Ok(());
    };
    cfg.selected_candidate_curve_cache.mark_decode_success();
    cfg.selected_candidate_curve_cache
        .mark_yellowstone_decode_success();
    cfg.selected_candidate_curve_cache.update(
        bonding_curve.clone(),
        state,
        account_update.slot,
        now_ms().min(u64::MAX as u128) as u64,
    );
    if let (Some(registry), Some(actions)) = (exit_watcher, exit_actions) {
        let active_exit_curve = registry
            .bonding_curves()
            .iter()
            .any(|curve| curve == &bonding_curve);
        if active_exit_curve {
            emit_selected_candidate_sniper_event(
                &cfg.selected_candidate_sniper,
                json!({
                    "kind": "rustSelectedCandidateCurveCacheExitBridge",
                    "atMs": now_ms(),
                    "bondingCurve": bonding_curve,
                    "slot": account_update.slot,
                    "source": "yellowstone-cache",
                }),
            );
        }
        handle_exit_curve_account_bytes(
            &bonding_curve,
            &account.data,
            account_update.slot,
            "yellowstone-cache",
            registry,
            actions,
            cfg,
        );
    }
    Ok(())
}

#[cfg(feature = "jito-shredstream")]
fn handle_selected_candidate_shredstream_submitted_buy_proof(
    cfg: &Config,
    tx: &VersionedTransaction,
    slot: u64,
    transaction_index: u64,
) {
    if !cfg.selected_candidate_sniper.enabled
        || !cfg.selected_candidate_sniper.balance_cache_enabled
    {
        return;
    }
    let Some(signature) = tx.signatures.first().map(|signature| signature.to_string()) else {
        return;
    };
    let observed_at_ms = now_ms().min(u64::MAX as u128) as u64;
    if let Some(cached) = cfg
        .selected_candidate_sniper
        .balance_cache
        .remember_shredstream_transaction_signature(&signature, slot, observed_at_ms)
    {
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateShredstreamSubmittedBuyProofUpdate",
                "atMs": now_ms(),
                "signature": signature,
                "mint": cached.mint,
                "ata": cached.ata,
                "tokenProgramId": cached.token_program_id,
                "amountRaw": cached.amount_raw,
                "slot": cached.slot,
                "transactionIndex": transaction_index,
                "observedAtMs": cached.observed_at_ms,
                "exact": cached.exact,
                "balanceSource": cached.source,
                "proofTiming": "shredstream_after_buy_registration",
            }),
        );
    }
}

#[cfg(feature = "jito-shredstream")]
fn handle_shredstream_exit_curve_mark_observations(
    cfg: &Config,
    observations: &[NativePumpfunObservation],
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
    feed_exits: bool,
) {
    if !cfg.selected_candidate_sniper.enabled || observations.is_empty() {
        return;
    }
    let (Some(registry), Some(actions)) = (exit_watcher, exit_actions) else {
        return;
    };
    let active_curves = registry
        .bonding_curves()
        .into_iter()
        .collect::<HashSet<_>>();
    let selector = &cfg.native_strict_selector;
    let now = now_ms().min(u64::MAX as u128) as u64;

    for observation in observations {
        let bridge = {
            let mut state = match selector.state.lock() {
                Ok(state) => state,
                Err(_) => return,
            };
            native_strict_expire(&mut state, selector.mint_ttl_ms, now);
            native_strict_enforce_capacity(&mut state, selector.max_mints);
            match observation {
                NativePumpfunObservation::Creation {
                    mint,
                    signature,
                    slot,
                    transaction_index: _transaction_index,
                    observed_at_ms,
                    token_program_id,
                    bonding_curve,
                } => {
                    let mint_state = state.mints.entry(mint.clone()).or_default();
                    let replace = mint_state
                        .creation
                        .as_ref()
                        .map(|old| (*slot, *observed_at_ms) < (old.slot, old.observed_at_ms))
                        .unwrap_or(true);
                    if replace {
                        mint_state.creation = Some(NativeCreationState {
                            signature: signature.clone(),
                            observed_at_ms: *observed_at_ms,
                            slot: *slot,
                            token_program_id: token_program_id.clone(),
                            bonding_curve: bonding_curve.clone(),
                        });
                    }
                    mint_state.last_observed_at_ms =
                        mint_state.last_observed_at_ms.max(*observed_at_ms);
                    None
                }
                NativePumpfunObservation::Flow(flow) => {
                    if flow.side == NativeFlowSide::Buy
                        && cfg
                            .selected_candidate_sniper
                            .balance_cache
                            .is_pending_submitted_buy_signature(&flow.signature)
                    {
                        None
                    } else {
                        let mint_state = state.mints.entry(flow.mint.clone()).or_default();
                        if mint_state.flows.iter().any(|seen| {
                            seen.signature == flow.signature
                                && seen.instruction_index == flow.instruction_index
                                && seen.side == flow.side
                        }) {
                            None
                        } else {
                            mint_state.sequence = mint_state.sequence.saturating_add(1);
                            mint_state.last_observed_at_ms =
                                mint_state.last_observed_at_ms.max(flow.observed_at_ms);
                            mint_state.flows.push(flow.clone());
                            mint_state.flows.sort_by_key(|item| {
                                (item.slot, item.transaction_index, item.instruction_index)
                            });
                            while mint_state.flows.len() > 64 {
                                mint_state.flows.remove(0);
                            }
                            let flows = mint_state.flows.clone();
                            active_curves.contains(&flow.bonding_curve).then(|| {
                                (
                                    flow.bonding_curve.clone(),
                                    flow.slot,
                                    flow.observed_at_ms,
                                    mint_state.sequence,
                                    flows,
                                )
                            })
                        }
                    }
                }
            }
        };

        let Some((bonding_curve, slot, observed_at_ms, sequence, flows)) = bridge else {
            continue;
        };
        let feature_sol_price_usd = selector.feature_sol_price_usd;
        let Some(features) = native_strict_features(&flows, observed_at_ms, feature_sol_price_usd)
        else {
            continue;
        };
        emit_selected_candidate_sniper_event(
            &cfg.selected_candidate_sniper,
            json!({
                "kind": "rustSelectedCandidateShredstreamExitCurveBridge",
                "atMs": now_ms(),
                "bondingCurve": bonding_curve,
                "slot": slot,
                "observedAtMs": observed_at_ms,
                "source": "native-shred-exit-flow",
                "sequence": sequence,
                "featureMarketCapUsd": features.feature_market_cap_usd,
                "featureSolPriceUsd": feature_sol_price_usd,
                "buyCount60": features.buy_count,
                "sellCount60": features.sell_count,
                "netBuySol60": features.net_buy_sol,
                "feedExits": feed_exits,
            }),
        );
        if feed_exits {
            handle_exit_curve_state(
                &bonding_curve,
                exit_watcher::CurveState {
                    virtual_token_reserves: features.feature_virtual_token_reserves,
                    virtual_sol_reserves: features.feature_virtual_sol_reserves,
                    complete: false,
                },
                slot,
                observed_at_ms,
                "native-shred-exit-flow",
                registry,
                actions,
                cfg,
            );
        }
    }
}

#[cfg(feature = "jito-shredstream")]
fn handle_shredstream_canonical_replay_exit_observations(
    cfg: &Config,
    observations: &[NativePumpfunObservation],
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
    feed_exits: bool,
    diagnostic: bool,
    seed_max_age_ms: u64,
    state_max_age_ms: u64,
) {
    if !cfg.selected_candidate_sniper.enabled || observations.is_empty() {
        return;
    }
    let (Some(registry), Some(actions)) = (exit_watcher, exit_actions) else {
        return;
    };
    let active_curves = registry
        .bonding_curves()
        .into_iter()
        .collect::<HashSet<_>>();
    if active_curves.is_empty() {
        return;
    }
    let sol_price = parse_env_optional_f64("RUST_SELECTED_CANDIDATE_SOL_PRICE_USD")
        .or(cfg.hot_sniper.sol_price_usd)
        .or_else(|| parse_env_optional_f64("SOL_PRICE_USD"))
        .or_else(|| parse_env_optional_f64("LOCAL_PUMPFUN_SOL_PRICE_USD"))
        .or_else(|| parse_env_optional_f64("SHRED_MC_SOL_PRICE_USD"))
        .unwrap_or(160.0);

    for observation in observations {
        let NativePumpfunObservation::Flow(flow) = observation else {
            continue;
        };
        if !active_curves.contains(&flow.bonding_curve) {
            continue;
        }
        if flow.side == NativeFlowSide::Buy
            && cfg
                .selected_candidate_sniper
                .balance_cache
                .is_pending_submitted_buy_signature(&flow.signature)
        {
            if diagnostic {
                emit_selected_candidate_sniper_event(
                    &cfg.selected_candidate_sniper,
                    json!({
                        "kind": "rustSelectedCandidateShredstreamCanonicalReplayExitBridgeSkipped",
                        "atMs": now_ms(),
                        "mint": flow.mint,
                        "bondingCurve": flow.bonding_curve,
                        "signature": flow.signature,
                        "slot": flow.slot,
                        "transactionIndex": flow.transaction_index,
                        "instructionIndex": flow.instruction_index,
                        "observedAtMs": flow.observed_at_ms,
                        "flowSide": "buy",
                        "reason": "pending_self_buy_not_landed",
                        "feedExits": feed_exits,
                        "seedMaxAgeMs": seed_max_age_ms,
                        "stateMaxAgeMs": state_max_age_ms,
                    }),
                );
            }
            continue;
        }
        match cfg
            .selected_candidate_shred_curve_replay
            .update_from_canonical_seed(
                &cfg.selected_candidate_curve_cache,
                flow,
                seed_max_age_ms,
                state_max_age_ms,
            ) {
            Ok(Some(update)) => {
                let replay_market_cap_usd = estimate_market_cap_sol(&update.state)
                    .map(|market_cap_sol| market_cap_sol * sol_price);
                if diagnostic {
                    emit_selected_candidate_sniper_event(
                        &cfg.selected_candidate_sniper,
                        json!({
                            "kind": "rustSelectedCandidateShredstreamCanonicalReplayExitBridge",
                            "atMs": now_ms(),
                            "mint": flow.mint,
                            "bondingCurve": flow.bonding_curve,
                            "signature": flow.signature,
                            "slot": flow.slot,
                            "transactionIndex": flow.transaction_index,
                            "instructionIndex": flow.instruction_index,
                            "observedAtMs": flow.observed_at_ms,
                            "flowSide": match flow.side {
                                NativeFlowSide::Buy => "buy",
                                NativeFlowSide::Sell => "sell",
                            },
                            "seedSlot": update.seed_slot,
                            "seedObservedAtMs": update.seed_observed_at_ms,
                            "seedSource": update.seed_source,
                            "lastSlot": update.last_slot,
                            "lastTransactionIndex": update.last_transaction_index,
                            "lastInstructionIndex": update.last_instruction_index,
                            "updatedAtMs": update.updated_at_ms,
                            "appliedFlow": update.applied_flow,
                            "replaySource": update.replay_source,
                            "replayMarketCapUsd": replay_market_cap_usd,
                            "feedExits": feed_exits,
                            "seedMaxAgeMs": seed_max_age_ms,
                            "stateMaxAgeMs": state_max_age_ms,
                        }),
                    );
                }
                if feed_exits {
                    handle_exit_curve_state(
                        &flow.bonding_curve,
                        exit_watcher::CurveState {
                            virtual_token_reserves: update.state.virtual_token_reserves,
                            virtual_sol_reserves: update.state.virtual_sol_reserves,
                            complete: update.state.complete,
                        },
                        update.last_slot,
                        update.updated_at_ms,
                        update.replay_source,
                        registry,
                        actions,
                        cfg,
                    );
                }
            }
            Ok(None) => {}
            Err(reason) => {
                if diagnostic {
                    emit_selected_candidate_sniper_event(
                        &cfg.selected_candidate_sniper,
                        json!({
                            "kind": "rustSelectedCandidateShredstreamCanonicalReplayExitBridgeSkipped",
                            "atMs": now_ms(),
                            "mint": flow.mint,
                            "bondingCurve": flow.bonding_curve,
                            "signature": flow.signature,
                            "slot": flow.slot,
                            "transactionIndex": flow.transaction_index,
                            "instructionIndex": flow.instruction_index,
                            "observedAtMs": flow.observed_at_ms,
                            "flowSide": match flow.side {
                                NativeFlowSide::Buy => "buy",
                                NativeFlowSide::Sell => "sell",
                            },
                            "reason": reason,
                            "feedExits": feed_exits,
                            "seedMaxAgeMs": seed_max_age_ms,
                            "stateMaxAgeMs": state_max_age_ms,
                        }),
                    );
                }
            }
        }
    }
}

#[cfg(feature = "jito-shredstream")]
async fn handle_native_strict_shred_observations(
    cfg: &Config,
    observations: Vec<NativePumpfunObservation>,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) {
    if observations.is_empty() {
        return;
    }
    for observation in observations {
        if cfg.selected_candidate_sniper.curve_cache_enabled
            && cfg.selected_candidate_sniper.curve_cache_owner_wide
        {
            match &observation {
                NativePumpfunObservation::Creation {
                    bonding_curve: Some(bonding_curve),
                    ..
                } => cfg.selected_candidate_curve_cache.watch(bonding_curve),
                NativePumpfunObservation::Flow(flow) => cfg
                    .selected_candidate_curve_cache
                    .watch(&flow.bonding_curve),
                _ => {}
            }
        }
        let candidate = match native_strict_ingest_observation(cfg, observation).await {
            Ok(candidate) => candidate,
            Err(err) => {
                selected_candidate_reject(
                    &cfg.selected_candidate_sniper,
                    "native_strict_selector_error",
                    json!({ "error": err.to_string() }),
                );
                None
            }
        };
        if let Some(candidate) = candidate {
            let shadow_comparison =
                native_strict_shadow_comparison(&cfg.native_strict_selector, &candidate);
            emit_selected_candidate_sniper_event(
                &cfg.selected_candidate_sniper,
                json!({
                    "kind": "rustNativeStrictCandidateSelected",
                    "schemaVersion": "rust-native-strict-selector-1",
                    "atMs": now_ms(),
                    "lane": cfg.selected_candidate_sniper.required_lane,
                    "policy": cfg.selected_candidate_sniper.required_policy,
                    "key": candidate.key,
                    "mint": candidate.mint,
                    "sourceSignature": candidate.source_signature,
                    "entryMarketCapUsd": candidate.entry_market_cap_usd,
                    "eventLagMs": candidate.event_lag_ms,
                    "reserveSource": candidate.reserve_source,
                    "reserveSlot": candidate.reserve_slot,
                    "reserveObservedAtMs": candidate.reserve_observed_at_ms,
                    "bondingCurve": candidate.bonding_curve,
                    "tokenProgramId": candidate.token_program_id,
                    "nativeFeatures": candidate.native_features.clone(),
                    "shadowComparison": shadow_comparison,
                }),
            );
            handle_selected_candidate_object(
                &cfg.selected_candidate_sniper,
                &cfg.hot_sniper,
                &cfg.selected_candidate_curve_cache,
                &cfg.selected_candidate_shred_curve_replay,
                exit_watcher,
                exit_actions,
                candidate,
            )
            .await;
        }
    }
}

#[cfg(feature = "jito-shredstream")]
async fn native_strict_ingest_observation(
    cfg: &Config,
    observation: NativePumpfunObservation,
) -> Result<Option<SelectedCandidate>> {
    let selector = &cfg.native_strict_selector;
    let selected_cfg = &cfg.selected_candidate_sniper;
    let now = now_ms().min(u64::MAX as u128) as u64;
    let maybe_flow = {
        let mut state = selector
            .state
            .lock()
            .map_err(|_| anyhow::anyhow!("native strict selector state poisoned"))?;
        native_strict_expire(&mut state, selector.mint_ttl_ms, now);
        native_strict_enforce_capacity(&mut state, selector.max_mints);
        state.counters.observations = state.counters.observations.saturating_add(1);
        match observation {
            NativePumpfunObservation::Creation {
                mint,
                signature,
                slot,
                transaction_index: _transaction_index,
                observed_at_ms,
                token_program_id,
                bonding_curve,
            } => {
                state.counters.creations = state.counters.creations.saturating_add(1);
                if token_program_id == TOKEN_2022_PROGRAM_ID {
                    state.counters.token2022_creations =
                        state.counters.token2022_creations.saturating_add(1);
                }
                let mint_state = state.mints.entry(mint).or_default();
                let replace = mint_state
                    .creation
                    .as_ref()
                    .map(|old| (slot, observed_at_ms) < (old.slot, old.observed_at_ms))
                    .unwrap_or(true);
                if replace {
                    mint_state.creation = Some(NativeCreationState {
                        signature,
                        observed_at_ms,
                        slot,
                        token_program_id,
                        bonding_curve,
                    });
                }
                mint_state.last_observed_at_ms = mint_state.last_observed_at_ms.max(observed_at_ms);
                None
            }
            NativePumpfunObservation::Flow(flow) => {
                state.counters.flows = state.counters.flows.saturating_add(1);
                if flow.side == NativeFlowSide::Buy {
                    state.counters.buys = state.counters.buys.saturating_add(1);
                } else {
                    state.counters.sells = state.counters.sells.saturating_add(1);
                }
                if flow.token_program_id == TOKEN_2022_PROGRAM_ID {
                    state.counters.token2022_flows =
                        state.counters.token2022_flows.saturating_add(1);
                }
                let mint_state = state.mints.entry(flow.mint.clone()).or_default();
                if mint_state.flows.iter().any(|seen| {
                    seen.signature == flow.signature
                        && seen.instruction_index == flow.instruction_index
                        && seen.side == flow.side
                }) {
                    native_strict_increment_reject(&mut state, "duplicate_flow");
                    return Ok(None);
                }
                mint_state.sequence = mint_state.sequence.saturating_add(1);
                mint_state.last_observed_at_ms =
                    mint_state.last_observed_at_ms.max(flow.observed_at_ms);
                mint_state.flows.push(flow.clone());
                mint_state.flows.sort_by_key(|item| {
                    (item.slot, item.transaction_index, item.instruction_index)
                });
                while mint_state.flows.len() > 64 {
                    mint_state.flows.remove(0);
                }
                Some((
                    flow,
                    mint_state.creation.clone(),
                    mint_state.flows.clone(),
                    mint_state.sequence,
                ))
            }
        }
    };
    let Some((flow, creation, flows, sequence)) = maybe_flow else {
        native_strict_emit_status_if_due(cfg, now);
        return Ok(None);
    };
    if flow.side != NativeFlowSide::Buy {
        native_strict_count_reject(selector, "non_buy_flow");
        native_strict_emit_status_if_due(cfg, now);
        return Ok(None);
    }
    if flow.token_program_id != TOKEN_2022_PROGRAM_ID {
        native_strict_count_reject(selector, "non_token2022_flow");
        native_strict_emit_status_if_due(cfg, now);
        return Ok(None);
    }
    let mayhem_account = env::var("MAYHEM_SKIP_ACCOUNT")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| "MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e".to_string());
    if flow
        .source_ix_accounts
        .iter()
        .any(|account| account == &mayhem_account)
    {
        native_strict_count_reject(selector, "mayhem_source_ix_account");
        selected_candidate_reject(
            selected_cfg,
            "native_strict_mayhem_source_ix_account",
            json!({ "mint": flow.mint, "mayhemAccount": mayhem_account }),
        );
        return Ok(None);
    }
    let Some(creation) = creation else {
        native_strict_count_reject(selector, "missing_creation");
        native_strict_emit_status_if_due(cfg, now);
        return Ok(None);
    };
    if creation.token_program_id != TOKEN_2022_PROGRAM_ID {
        native_strict_count_reject(selector, "non_token2022_creation");
        native_strict_emit_status_if_due(cfg, now);
        return Ok(None);
    }
    if selected_cfg.curve_cache_enabled && !selected_cfg.curve_cache_owner_wide {
        cfg.selected_candidate_curve_cache
            .watch(&flow.bonding_curve);
    }
    let creation_age_ms = flow.observed_at_ms.saturating_sub(creation.observed_at_ms);
    let Some(features) =
        native_strict_features(&flows, flow.observed_at_ms, selector.feature_sol_price_usd)
    else {
        native_strict_count_reject(selector, "missing_features");
        native_strict_emit_status_if_due(cfg, now);
        return Ok(None);
    };
    if selected_cfg.curve_cache_enabled {
        cfg.selected_candidate_curve_cache
            .update_from_native_feature(
                flow.bonding_curve.clone(),
                features.feature_virtual_sol_reserves,
                features.feature_virtual_token_reserves,
                flow.slot,
                flow.observed_at_ms,
            );
    }
    if let Some(reason) = native_strict_pre_gate_reject_reason(selector, &features, creation_age_ms)
    {
        native_strict_count_reject(selector, reason);
        native_strict_emit_status_if_due(cfg, now);
        return Ok(None);
    }
    if let Some(reason) =
        native_strict_parity_guard_reject_reason(selector, &flow.mint, flow.observed_at_ms).await
    {
        native_strict_count_reject(selector, reason);
        selected_candidate_reject(
            selected_cfg,
            reason,
            json!({
                "mint": flow.mint,
                "sourceSignature": flow.signature,
                "flowObservedAtMs": flow.observed_at_ms,
                "parityMaxAgeMs": selector.parity_max_age_ms,
                "parityWaitMs": selector.parity_wait_ms,
            }),
        );
        native_strict_emit_status_if_due(cfg, now);
        return Ok(None);
    }
    native_strict_count_pre_gate_pass(selector);
    let key = format!(
        "rust-native-strict-v1:{}:{}#{}",
        flow.mint, creation.signature, sequence
    );
    {
        let state = selector
            .state
            .lock()
            .map_err(|_| anyhow::anyhow!("native strict selector state poisoned"))?;
        if state.accepted_keys.contains(&key) {
            drop(state);
            native_strict_count_reject(selector, "duplicate_candidate_key");
            return Ok(None);
        }
        if state.accepted_mints.contains(&flow.mint) {
            drop(state);
            native_strict_count_reject(selector, "duplicate_candidate_mint");
            return Ok(None);
        }
    }
    native_strict_count_curve_check(selector);
    let curve_snapshot = match selected_candidate_curve_snapshot(
        selected_cfg,
        &cfg.hot_sniper,
        &cfg.selected_candidate_curve_cache,
        &key,
        &flow.mint,
        &flow.bonding_curve,
    )
    .await
    {
        Ok(snapshot) => snapshot,
        Err(err)
            if !selected_cfg.curve_rpc_fallback_enabled
                && err
                    .to_string()
                    .contains("selected candidate curve cache miss") =>
        {
            if parse_env_bool(
                "RUST_SELECTED_CANDIDATE_SEND_TIME_CURVE_CACHE_REQUIRED",
                false,
            ) {
                native_strict_count_reject(selector, "local_curve_cache_required");
                selected_candidate_reject(
                    selected_cfg,
                    "native_strict_local_curve_cache_required",
                    json!({
                        "key": key,
                        "mint": flow.mint,
                        "bondingCurve": flow.bonding_curve,
                        "curveCacheWaitMs": selected_cfg.curve_cache_wait_ms,
                        "curveCacheMaxAgeMs": selected_cfg.curve_cache_max_age_ms,
                        "sourceTemplateFallbackEnabled": selector.source_template_cache_miss_fallback,
                    }),
                );
                native_strict_emit_status_if_due(cfg, now);
                return Ok(None);
            }
            if selector.source_template_cache_miss_fallback {
                if let Err(template_err) =
                    executor::build_pumpfun_exit_sell_template_from_source_accounts(
                        &flow.mint,
                        &flow.source_ix_accounts,
                        Some(TOKEN_2022_PROGRAM_ID),
                    )
                {
                    native_strict_count_reject(selector, "source_template_unavailable");
                    selected_candidate_reject(
                        selected_cfg,
                        "native_strict_source_template_unavailable",
                        json!({
                            "key": key,
                            "mint": flow.mint,
                            "bondingCurve": flow.bonding_curve,
                            "error": template_err.to_string(),
                        }),
                    );
                    native_strict_emit_status_if_due(cfg, now);
                    return Ok(None);
                }
                let sol_price = parse_env_optional_f64("RUST_SELECTED_CANDIDATE_SOL_PRICE_USD")
                    .or(cfg.hot_sniper.sol_price_usd)
                    .or_else(|| parse_env_optional_f64("SOL_PRICE_USD"))
                    .or_else(|| parse_env_optional_f64("LOCAL_PUMPFUN_SOL_PRICE_USD"))
                    .or_else(|| parse_env_optional_f64("SHRED_MC_SOL_PRICE_USD"))
                    .unwrap_or(160.0);
                let direct_mc_usd = native_strict_market_cap_usd_from_reserves(
                    features.feature_virtual_sol_reserves,
                    features.feature_virtual_token_reserves,
                    sol_price,
                )
                .context("native strict fallback direct MC unavailable")?;
                if selected_cfg
                    .max_entry_mc_usd
                    .is_some_and(|max| direct_mc_usd > max)
                    || selected_cfg
                        .min_entry_mc_usd
                        .is_some_and(|min| direct_mc_usd < min)
                {
                    native_strict_count_reject(selector, "direct_mc_gate");
                    selected_candidate_reject(
                        selected_cfg,
                        "native_strict_direct_mc_gate",
                        json!({
                            "key": key,
                            "mint": flow.mint,
                            "directEntryMarketCapUsd": direct_mc_usd,
                            "minEntryMcUsd": selected_cfg.min_entry_mc_usd,
                            "maxEntryMcUsd": selected_cfg.max_entry_mc_usd,
                            "reserveSource": "native-source-template-simulated",
                        }),
                    );
                    return Ok(None);
                }
                {
                    let mut state = selector
                        .state
                        .lock()
                        .map_err(|_| anyhow::anyhow!("native strict selector state poisoned"))?;
                    if state.accepted_keys.contains(&key) {
                        native_strict_increment_reject(&mut state, "duplicate_candidate_key");
                        return Ok(None);
                    }
                    if state.accepted_mints.contains(&flow.mint) {
                        native_strict_increment_reject(&mut state, "duplicate_candidate_mint");
                        return Ok(None);
                    }
                    state.accepted_keys.insert(key.clone());
                    state.accepted_mints.insert(flow.mint.clone());
                    if state.accepted_keys.len() > selector.max_mints.saturating_mul(4) {
                        state.accepted_keys.clear();
                        state.accepted_mints.clear();
                    }
                }
                native_strict_count_selected(selector);
                let quote_tokens = quote_buy_tokens_out_raw_main(
                    features.feature_virtual_sol_reserves as u128,
                    features.feature_virtual_token_reserves as u128,
                    selected_cfg.buy_lamports as u128,
                )?;
                let native_features = json!({
                    "featureMarketCapUsd": features.feature_market_cap_usd,
                    "featureSolPriceUsd": selector.feature_sol_price_usd,
                    "directEntryMarketCapUsd": direct_mc_usd,
                    "creationAgeMs": creation_age_ms,
                    "netBuySol60": features.net_buy_sol,
                    "buyCount60": features.buy_count,
                    "sellCount60": features.sell_count,
                    "sellPressure60": features.sell_pressure,
                    "dominantBuyShare60": features.dominant_buy_share,
                    "uniqueWallets60": features.unique_wallets,
                    "uniquePerBuy60": features.unique_per_buy,
                    "buyCount1000ms": features.buy_count_1000ms,
                    "interBuyMedianMs": features.inter_buy_median_ms,
                    "firstBuyerQuoteShareBps": features.first_buyer_quote_share_bps,
                    "sequence": sequence,
                    "reserveSource": "native-source-template-simulated",
                });
                return Ok(Some(SelectedCandidate {
                    key,
                    mint: flow.mint,
                    source_signature: flow.signature,
                    source_wallet: "rust-native-program-wide-selector".to_string(),
                    entry_market_cap_usd: direct_mc_usd,
                    event_lag_ms: now_ms().saturating_sub(flow.observed_at_ms as u128),
                    bonding_curve: flow.bonding_curve,
                    creator: "source-template-only".to_string(),
                    token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
                    quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
                    virtual_sol_reserves: features.feature_virtual_sol_reserves,
                    virtual_token_reserves: features.feature_virtual_token_reserves,
                    shadow_tokens_out_raw: u64::try_from(quote_tokens)
                        .context("quote tokens exceed u64")?,
                    shadow_sol_in_lamports: selected_cfg.buy_lamports,
                    reserve_slot: None,
                    reserve_observed_at_ms: Some(flow.observed_at_ms),
                    reserve_source: "native-source-template-simulated",
                    native_features: Some(native_features),
                    source_ix_accounts: Some(flow.source_ix_accounts),
                    source_ix_data_base64: Some(flow.source_ix_data_base64),
                }));
            }
            native_strict_count_reject(selector, "local_curve_cache_miss");
            selected_candidate_reject(
                selected_cfg,
                "native_strict_local_curve_cache_miss",
                json!({
                    "key": key,
                    "mint": flow.mint,
                    "bondingCurve": flow.bonding_curve,
                    "curveCacheWaitMs": selected_cfg.curve_cache_wait_ms,
                    "curveCacheMaxAgeMs": selected_cfg.curve_cache_max_age_ms,
                }),
            );
            native_strict_emit_status_if_due(cfg, now);
            return Ok(None);
        }
        Err(err) => return Err(err),
    };
    let state = curve_snapshot.state.clone();
    if state.complete {
        native_strict_count_reject(selector, "curve_complete");
        selected_candidate_reject(
            selected_cfg,
            "native_strict_curve_complete",
            json!({ "key": key, "mint": flow.mint, "bondingCurve": flow.bonding_curve }),
        );
        return Ok(None);
    }
    let sol_price = parse_env_optional_f64("RUST_SELECTED_CANDIDATE_SOL_PRICE_USD")
        .or(cfg.hot_sniper.sol_price_usd)
        .or_else(|| parse_env_optional_f64("SOL_PRICE_USD"))
        .or_else(|| parse_env_optional_f64("LOCAL_PUMPFUN_SOL_PRICE_USD"))
        .or_else(|| parse_env_optional_f64("SHRED_MC_SOL_PRICE_USD"))
        .unwrap_or(160.0);
    let direct_mc_usd =
        estimate_market_cap_sol(&state).context("native strict direct MC unavailable")? * sol_price;
    if selected_cfg
        .max_entry_mc_usd
        .is_some_and(|max| direct_mc_usd > max)
        || selected_cfg
            .min_entry_mc_usd
            .is_some_and(|min| direct_mc_usd < min)
    {
        native_strict_count_reject(selector, "direct_mc_gate");
        selected_candidate_reject(
            selected_cfg,
            "native_strict_direct_mc_gate",
            json!({
                "key": key,
                "mint": flow.mint,
                "directEntryMarketCapUsd": direct_mc_usd,
                "minEntryMcUsd": selected_cfg.min_entry_mc_usd,
                "maxEntryMcUsd": selected_cfg.max_entry_mc_usd,
            }),
        );
        return Ok(None);
    }
    {
        let mut state = selector
            .state
            .lock()
            .map_err(|_| anyhow::anyhow!("native strict selector state poisoned"))?;
        if state.accepted_keys.contains(&key) {
            native_strict_increment_reject(&mut state, "duplicate_candidate_key");
            return Ok(None);
        }
        if state.accepted_mints.contains(&flow.mint) {
            native_strict_increment_reject(&mut state, "duplicate_candidate_mint");
            return Ok(None);
        }
        state.accepted_keys.insert(key.clone());
        state.accepted_mints.insert(flow.mint.clone());
        if state.accepted_keys.len() > selector.max_mints.saturating_mul(4) {
            state.accepted_keys.clear();
            state.accepted_mints.clear();
        }
    }
    native_strict_count_selected(selector);
    let creator = state
        .creator
        .clone()
        .unwrap_or_else(|| "source-template-only".to_string());
    let quote_tokens = quote_buy_tokens_out_raw_main(
        state.virtual_sol_reserves as u128,
        state.virtual_token_reserves as u128,
        selected_cfg.buy_lamports as u128,
    )?;
    let native_features = json!({
        "featureMarketCapUsd": features.feature_market_cap_usd,
        "featureSolPriceUsd": selector.feature_sol_price_usd,
        "directEntryMarketCapUsd": direct_mc_usd,
        "creationAgeMs": creation_age_ms,
        "netBuySol60": features.net_buy_sol,
        "buyCount60": features.buy_count,
        "sellCount60": features.sell_count,
        "sellPressure60": features.sell_pressure,
        "dominantBuyShare60": features.dominant_buy_share,
        "uniqueWallets60": features.unique_wallets,
        "uniquePerBuy60": features.unique_per_buy,
        "buyCount1000ms": features.buy_count_1000ms,
        "interBuyMedianMs": features.inter_buy_median_ms,
        "firstBuyerQuoteShareBps": features.first_buyer_quote_share_bps,
        "sequence": sequence,
    });
    Ok(Some(SelectedCandidate {
        key,
        mint: flow.mint,
        source_signature: flow.signature,
        source_wallet: "rust-native-program-wide-selector".to_string(),
        entry_market_cap_usd: direct_mc_usd,
        event_lag_ms: now_ms().saturating_sub(flow.observed_at_ms as u128),
        bonding_curve: flow.bonding_curve,
        creator,
        token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
        quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
        virtual_sol_reserves: state.virtual_sol_reserves,
        virtual_token_reserves: state.virtual_token_reserves,
        shadow_tokens_out_raw: u64::try_from(quote_tokens).context("quote tokens exceed u64")?,
        shadow_sol_in_lamports: selected_cfg.buy_lamports,
        reserve_slot: curve_snapshot.slot,
        reserve_observed_at_ms: Some(curve_snapshot.observed_at_ms),
        reserve_source: curve_snapshot.source,
        native_features: Some(native_features),
        source_ix_accounts: Some(flow.source_ix_accounts),
        source_ix_data_base64: Some(flow.source_ix_data_base64),
    }))
}

#[derive(Debug)]
struct NativeStrictFeatures {
    feature_market_cap_usd: f64,
    feature_virtual_sol_reserves: u64,
    feature_virtual_token_reserves: u64,
    net_buy_sol: f64,
    buy_count: u64,
    sell_count: u64,
    sell_pressure: f64,
    dominant_buy_share: f64,
    unique_wallets: u64,
    unique_per_buy: f64,
    buy_count_1000ms: u64,
    inter_buy_median_ms: u64,
    first_buyer_quote_share_bps: u64,
}

fn native_strict_features(
    flows: &[NativeFlowEvent],
    observed_at_ms: u64,
    feature_sol_price_usd: f64,
) -> Option<NativeStrictFeatures> {
    let recent = flows
        .iter()
        .filter(|flow| observed_at_ms.saturating_sub(flow.observed_at_ms) <= 60_000)
        .collect::<Vec<_>>();
    let buys = recent
        .iter()
        .copied()
        .filter(|flow| flow.side == NativeFlowSide::Buy)
        .collect::<Vec<_>>();
    if buys.is_empty() {
        return None;
    }
    let buy_quote = buys
        .iter()
        .try_fold(0u64, |sum, flow| sum.checked_add(flow.quote_lamports?))?;
    let sell_count = recent
        .iter()
        .filter(|flow| flow.side == NativeFlowSide::Sell)
        .count() as u64;
    let feature_curve = native_strict_feature_curve(&recent, feature_sol_price_usd)?;
    let sell_quote = feature_curve.sell_quote_lamports;
    let max_buy = buys
        .iter()
        .filter_map(|flow| flow.quote_lamports)
        .max()
        .unwrap_or_default();
    let mut wallets = HashSet::new();
    for buy in &buys {
        wallets.insert(buy.wallet.as_str());
    }
    let first_wallet = buys.first()?.wallet.as_str();
    let first_wallet_quote = buys
        .iter()
        .filter(|flow| flow.wallet == first_wallet)
        .filter_map(|flow| flow.quote_lamports)
        .sum::<u64>();
    let mut inter_buy = buys
        .windows(2)
        .map(|pair| {
            pair[1]
                .observed_at_ms
                .saturating_sub(pair[0].observed_at_ms)
        })
        .collect::<Vec<_>>();
    inter_buy.sort_unstable();
    let inter_buy_median_ms = if inter_buy.is_empty() {
        u64::MAX
    } else {
        inter_buy[inter_buy.len() / 2]
    };
    let buy_count_1000ms = buys
        .iter()
        .filter(|flow| observed_at_ms.saturating_sub(flow.observed_at_ms) <= 1_000)
        .count() as u64;
    Some(NativeStrictFeatures {
        feature_market_cap_usd: feature_curve.market_cap_usd,
        feature_virtual_sol_reserves: feature_curve.virtual_sol_reserves,
        feature_virtual_token_reserves: feature_curve.virtual_token_reserves,
        net_buy_sol: (buy_quote.saturating_sub(sell_quote)) as f64 / LAMPORTS_PER_SOL,
        buy_count: buys.len() as u64,
        sell_count,
        sell_pressure: if buy_quote.saturating_add(sell_quote) == 0 {
            1.0
        } else {
            sell_quote as f64 / buy_quote.saturating_add(sell_quote) as f64
        },
        dominant_buy_share: if buy_quote == 0 {
            1.0
        } else {
            max_buy as f64 / buy_quote as f64
        },
        unique_wallets: wallets.len() as u64,
        unique_per_buy: wallets.len() as f64 / buys.len() as f64,
        buy_count_1000ms,
        inter_buy_median_ms,
        first_buyer_quote_share_bps: if buy_quote == 0 {
            10_000
        } else {
            first_wallet_quote.saturating_mul(10_000) / buy_quote
        },
    })
}

struct NativeStrictFeatureCurve {
    market_cap_usd: f64,
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    sell_quote_lamports: u64,
}

fn native_strict_feature_curve(
    flows: &[&NativeFlowEvent],
    sol_price_usd: f64,
) -> Option<NativeStrictFeatureCurve> {
    if !sol_price_usd.is_finite() || sol_price_usd <= 0.0 {
        return None;
    }
    let mut virtual_sol_reserves = PUMPFUN_INITIAL_VIRTUAL_SOL_RESERVES as u128;
    let mut virtual_token_reserves = PUMPFUN_INITIAL_VIRTUAL_TOKEN_RESERVES as u128;
    let mut sell_quote_lamports = 0u64;
    let mut ordered = flows.to_vec();
    ordered.sort_by_key(|flow| (flow.slot, flow.transaction_index, flow.instruction_index));
    for flow in ordered {
        match flow.side {
            NativeFlowSide::Buy => {
                let quote = flow.quote_lamports? as u128;
                let reserve_quote = pumpfun_exact_sol_in_curve_lamports_main(quote).ok()?;
                let tokens_out = quote_buy_tokens_out_raw_main(
                    virtual_sol_reserves,
                    virtual_token_reserves,
                    quote,
                )
                .ok()?;
                virtual_sol_reserves = virtual_sol_reserves.checked_add(reserve_quote)?;
                virtual_token_reserves = virtual_token_reserves.checked_sub(tokens_out)?;
            }
            NativeFlowSide::Sell => {
                let tokens_in = flow.sell_token_amount_raw? as u128;
                if tokens_in == 0 {
                    return None;
                }
                let pre_sol = virtual_sol_reserves;
                let pre_tokens = virtual_token_reserves;
                let post_tokens = pre_tokens.checked_add(tokens_in)?;
                let invariant = pre_sol.checked_mul(pre_tokens)?;
                let post_sol = invariant / post_tokens;
                let gross = pre_sol.checked_sub(post_sol)?;
                let fee = gross.checked_mul(PUMP_FUN_SELL_TRADE_FEE_BPS)? / 10_000;
                let net = gross.checked_sub(fee)?;
                sell_quote_lamports =
                    sell_quote_lamports.saturating_add(net.min(u64::MAX as u128) as u64);
                virtual_sol_reserves = post_sol;
                virtual_token_reserves = post_tokens;
            }
        }
    }
    if virtual_sol_reserves == 0 || virtual_token_reserves == 0 {
        return None;
    }
    let market_cap_sol = (virtual_sol_reserves as f64 / LAMPORTS_PER_SOL)
        * (PUMPFUN_TOKEN_TOTAL_SUPPLY as f64 / virtual_token_reserves as f64);
    let market_cap_usd = market_cap_sol * sol_price_usd;
    market_cap_usd
        .is_finite()
        .then_some(NativeStrictFeatureCurve {
            market_cap_usd,
            virtual_sol_reserves: u64::try_from(virtual_sol_reserves).ok()?,
            virtual_token_reserves: u64::try_from(virtual_token_reserves).ok()?,
            sell_quote_lamports,
        })
}

fn native_strict_market_cap_usd_from_reserves(
    virtual_sol_reserves: u64,
    virtual_token_reserves: u64,
    sol_price_usd: f64,
) -> Option<f64> {
    if virtual_sol_reserves == 0
        || virtual_token_reserves == 0
        || !sol_price_usd.is_finite()
        || sol_price_usd <= 0.0
    {
        return None;
    }
    let market_cap_sol = (virtual_sol_reserves as f64 / LAMPORTS_PER_SOL)
        * (PUMPFUN_TOKEN_TOTAL_SUPPLY as f64 / virtual_token_reserves as f64);
    let market_cap_usd = market_cap_sol * sol_price_usd;
    market_cap_usd.is_finite().then_some(market_cap_usd)
}

fn native_strict_pre_gate_reject_reason(
    selector: &NativeStrictSelectorConfig,
    features: &NativeStrictFeatures,
    creation_age_ms: u64,
) -> Option<&'static str> {
    if creation_age_ms < 500 {
        return Some("age_lt_500ms");
    }
    if let Some(min_feature_mc_usd) = selector.min_feature_mc_usd {
        if features.feature_market_cap_usd < min_feature_mc_usd {
            return Some("feature_mc_lt_min");
        }
    }
    if let Some(max_feature_mc_usd) = selector.max_feature_mc_usd {
        if features.feature_market_cap_usd > max_feature_mc_usd {
            return Some("feature_mc_gt_max");
        }
    }
    if features.net_buy_sol < 3.0 {
        return Some("net_buy_lt_3");
    }
    if features.net_buy_sol > 12.0 {
        return Some("net_buy_gt_12");
    }
    if features.net_buy_sol >= 5.0 && features.net_buy_sol < 7.0 {
        return Some("mid_net_5_7_excluded");
    }
    if features.buy_count < 3 {
        return Some("buy_count_lt_3");
    }
    if features.buy_count > 15 {
        return Some("buy_count_gt_15");
    }
    if features.sell_pressure > 0.02 {
        return Some("sell_pressure_gt_002");
    }
    if features.dominant_buy_share > 0.4 {
        return Some("dominant_buy_share_gt_040");
    }
    if features.unique_wallets < 3 {
        return Some("unique_wallets_lt_3");
    }
    if features.unique_per_buy < 0.7 {
        return Some("unique_per_buy_lt_070");
    }
    if features.buy_count_1000ms < 3 {
        return Some("buy_count_1000ms_lt_3");
    }
    if features.inter_buy_median_ms > 10 {
        return Some("inter_buy_median_gt_10ms");
    }
    if features.first_buyer_quote_share_bps > 4_000 {
        return Some("first_buyer_share_gt_4000bps");
    }
    None
}

fn native_strict_parity_guard_reject_reason_once(
    selector: &NativeStrictSelectorConfig,
    mint: &str,
    flow_observed_at_ms: u64,
) -> Option<&'static str> {
    selector.parity_candidate_source.as_ref()?;
    let candidate = selector
        .parity_candidates
        .lock()
        .ok()
        .and_then(|candidates| candidates.get(mint).cloned());
    let Some(candidate) = candidate else {
        return Some("native_strict_parity_guard_miss");
    };
    if candidate.observed_at_ms > flow_observed_at_ms.saturating_add(250) {
        return Some("native_strict_parity_guard_future");
    }
    if flow_observed_at_ms.saturating_sub(candidate.observed_at_ms) > selector.parity_max_age_ms {
        return Some("native_strict_parity_guard_stale");
    }
    None
}

async fn native_strict_parity_guard_reject_reason(
    selector: &NativeStrictSelectorConfig,
    mint: &str,
    flow_observed_at_ms: u64,
) -> Option<&'static str> {
    let mut reason =
        native_strict_parity_guard_reject_reason_once(selector, mint, flow_observed_at_ms);
    if reason != Some("native_strict_parity_guard_miss") || selector.parity_wait_ms == 0 {
        return reason;
    }

    let started = now_ms();
    loop {
        if now_ms().saturating_sub(started) >= selector.parity_wait_ms as u128 {
            return reason;
        }
        sleep(Duration::from_millis(25)).await;
        reason = native_strict_parity_guard_reject_reason_once(selector, mint, flow_observed_at_ms);
        if reason != Some("native_strict_parity_guard_miss") {
            return reason;
        }
    }
}

fn native_strict_shadow_comparison(
    selector: &NativeStrictSelectorConfig,
    candidate: &SelectedCandidate,
) -> Value {
    if selector.parity_candidate_source.is_none() && selector.comparison_candidate_source.is_none()
    {
        return Value::Null;
    }
    let candidate_observed_at_ms = candidate.reserve_observed_at_ms.unwrap_or_else(|| {
        now_ms()
            .saturating_sub(candidate.event_lag_ms)
            .min(u64::MAX as u128) as u64
    });
    let shadow = selector
        .parity_candidates
        .lock()
        .ok()
        .and_then(|candidates| candidates.get(&candidate.mint).cloned());
    let Some(shadow) = shadow else {
        return json!({
            "mode": if selector.parity_candidate_source.is_some() { "parity-guard" } else { "comparison-only" },
            "matched": false,
            "reason": "shadow_miss",
        });
    };
    let native_after_shadow_ms = candidate_observed_at_ms.saturating_sub(shadow.observed_at_ms);
    let shadow_after_native_ms = shadow
        .observed_at_ms
        .saturating_sub(candidate_observed_at_ms);
    json!({
        "mode": if selector.parity_candidate_source.is_some() { "parity-guard" } else { "comparison-only" },
        "matched": native_after_shadow_ms <= selector.parity_max_age_ms || shadow_after_native_ms <= 250,
        "shadowObservedAtMs": shadow.observed_at_ms,
        "nativeObservedAtMs": candidate_observed_at_ms,
        "nativeAfterShadowMs": native_after_shadow_ms,
        "shadowAfterNativeMs": shadow_after_native_ms,
        "shadowSourceSignature": shadow.source_signature,
        "shadowFeatureMarketCapUsd": shadow.feature_market_cap_usd,
        "maxAgeMs": selector.parity_max_age_ms,
    })
}

fn native_strict_increment_reject(state: &mut NativeStrictState, reason: &'static str) {
    let entry = state
        .counters
        .rejects
        .entry(reason.to_string())
        .or_insert(0);
    *entry = entry.saturating_add(1);
}

fn native_strict_count_reject(selector: &NativeStrictSelectorConfig, reason: &'static str) {
    if let Ok(mut state) = selector.state.lock() {
        native_strict_increment_reject(&mut state, reason);
    }
}

fn native_strict_count_pre_gate_pass(selector: &NativeStrictSelectorConfig) {
    if let Ok(mut state) = selector.state.lock() {
        state.counters.pre_gate_passes = state.counters.pre_gate_passes.saturating_add(1);
    }
}

fn native_strict_count_curve_check(selector: &NativeStrictSelectorConfig) {
    if let Ok(mut state) = selector.state.lock() {
        state.counters.curve_checks = state.counters.curve_checks.saturating_add(1);
    }
}

fn native_strict_count_selected(selector: &NativeStrictSelectorConfig) {
    if let Ok(mut state) = selector.state.lock() {
        state.counters.selected = state.counters.selected.saturating_add(1);
    }
}

fn native_strict_emit_status_if_due(cfg: &Config, now: u64) {
    let selector = &cfg.native_strict_selector;
    let event = {
        let Ok(mut state) = selector.state.lock() else {
            return;
        };
        if now.saturating_sub(state.last_status_at_ms) < selector.status_interval_ms {
            return;
        }
        state.last_status_at_ms = now;
        Some(json!({
            "kind": "rustNativeStrictSelectorStatus",
            "schemaVersion": "rust-native-strict-selector-status-1",
            "atMs": now,
            "activeMints": state.mints.len(),
            "acceptedKeys": state.accepted_keys.len(),
            "acceptedMints": state.accepted_mints.len(),
            "featureSolPriceUsd": selector.feature_sol_price_usd,
            "minFeatureMcUsd": selector.min_feature_mc_usd,
            "maxFeatureMcUsd": selector.max_feature_mc_usd,
            "minDirectEntryMcUsd": cfg.selected_candidate_sniper.min_entry_mc_usd,
            "maxDirectEntryMcUsd": cfg.selected_candidate_sniper.max_entry_mc_usd,
            "parityGuardEnabled": selector.parity_candidate_source.is_some(),
            "parityWaitMs": selector.parity_wait_ms,
            "comparisonFeedEnabled": selector.comparison_candidate_source.is_some(),
            "counters": state.counters.clone(),
        }))
    };
    if let Some(event) = event {
        emit_selected_candidate_sniper_event(&cfg.selected_candidate_sniper, event);
    }
}

fn native_strict_expire(state: &mut NativeStrictState, ttl_ms: u64, now_ms: u64) {
    state
        .mints
        .retain(|_, mint_state| now_ms.saturating_sub(mint_state.last_observed_at_ms) <= ttl_ms);
}

fn native_strict_enforce_capacity(state: &mut NativeStrictState, max_mints: usize) {
    if state.mints.len() <= max_mints {
        return;
    }
    let mut by_age = state
        .mints
        .iter()
        .map(|(mint, state)| (mint.clone(), state.last_observed_at_ms))
        .collect::<Vec<_>>();
    by_age.sort_by_key(|(_, observed)| *observed);
    let remove_count = state.mints.len().saturating_sub(max_mints);
    for (mint, _) in by_age.into_iter().take(remove_count) {
        state.mints.remove(&mint);
    }
}

#[cfg(feature = "jito-shredstream")]
async fn run_jito_shredstream_once(
    cfg: &Config,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
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
                handle_selected_candidate_shredstream_submitted_buy_proof(
                    cfg,
                    &tx,
                    slot_entry.slot,
                    tx_index,
                );
                let native_observations =
                    extract_native_pumpfun_observations(&tx, slot_entry.slot, tx_index);
                emit_teacher_impulse_observation_feed(cfg, &native_observations)?;
                let shred_canonical_replay_exit_enabled = parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_SHREDSTREAM_CANONICAL_REPLAY_EXIT_ENABLED",
                    false,
                );
                let shred_canonical_replay_diagnostic_enabled = parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_SHREDSTREAM_CANONICAL_REPLAY_DIAGNOSTIC_ENABLED",
                    true,
                );
                if shred_canonical_replay_exit_enabled || shred_canonical_replay_diagnostic_enabled
                {
                    handle_shredstream_canonical_replay_exit_observations(
                        cfg,
                        &native_observations,
                        exit_watcher,
                        exit_actions,
                        shred_canonical_replay_exit_enabled,
                        shred_canonical_replay_diagnostic_enabled,
                        parse_env_u64(
                            "RUST_SELECTED_CANDIDATE_SHREDSTREAM_CANONICAL_REPLAY_SEED_MAX_AGE_MS",
                            500,
                        ),
                        parse_env_u64(
                            "RUST_SELECTED_CANDIDATE_SHREDSTREAM_CANONICAL_REPLAY_STATE_MAX_AGE_MS",
                            3000,
                        ),
                    );
                }
                let shred_exit_curve_bridge_enabled = parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_SHREDSTREAM_EXIT_CURVE_BRIDGE_ENABLED",
                    false,
                );
                let shred_exit_curve_bridge_diagnostic_enabled = parse_env_bool(
                    "RUST_SELECTED_CANDIDATE_SHREDSTREAM_EXIT_CURVE_BRIDGE_DIAGNOSTIC_ENABLED",
                    true,
                );
                if !cfg.native_strict_selector.enabled
                    && (shred_exit_curve_bridge_enabled
                        || shred_exit_curve_bridge_diagnostic_enabled)
                {
                    handle_shredstream_exit_curve_mark_observations(
                        cfg,
                        &native_observations,
                        exit_watcher,
                        exit_actions,
                        shred_exit_curve_bridge_enabled,
                    );
                }
                if cfg.native_strict_selector.enabled && cfg.selected_candidate_sniper.enabled {
                    handle_native_strict_shred_observations(
                        cfg,
                        native_observations,
                        exit_watcher,
                        exit_actions,
                    )
                    .await;
                }
                if let Some(line) =
                    extract_shred_swap(&tx, &cfg.tracked_wallets, slot_entry.slot, tx_index)
                {
                    emit_detected_line(cfg, line, exit_watcher, exit_actions).await?;
                }
                tx_index = tx_index.saturating_add(1);
            }
        }
    }
    Ok(())
}

async fn emit_detected_line(
    cfg: &Config,
    line: DetectedSwapLine,
    exit_watcher: Option<&exit_watcher::ExitWatcherRegistry>,
    exit_actions: Option<&UnboundedSender<exit_watcher::ExitAction>>,
) -> Result<()> {
    let is_source_sell = line.input_mint != WRAPPED_SOL_MINT
        && line.input_mint != USDC_MINT
        && (line.output_mint == WRAPPED_SOL_MINT || line.output_mint == USDC_MINT);
    if is_source_sell {
        if let Some(registry) = exit_watcher {
            let action = registry.on_source_sell(
                &line.wallet,
                &line.input_mint,
                &line.signature,
                Some(line.amount_raw),
                now_ms().min(u64::MAX as u128) as u64,
            );
            if let (Some(action), Some(tx)) = (action, exit_actions) {
                let _ = tx.send(action);
            }
        }
    }
    if line.source_pump_swap_instruction.is_some() || line.source_pump_swap_accounts.is_some() {
        println!("{}", serde_json::to_string(&line)?);
        return Ok(());
    }

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
                let fallback_line = HotBuyFallbackLine {
                    kind: "hotBuyFallback",
                    source: "rust-hot-sniper",
                    detected_at_ms: now_ms(),
                    source_signature: line.signature.clone(),
                    wallet: line.wallet.clone(),
                    mint: line.output_mint.clone(),
                    reason,
                    source_slot: line.source_slot,
                    source_transaction_index: line.source_transaction_index,
                };
                println!("{}", serde_json::to_string(&fallback_line)?);
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
        return HotDecision::Fallback(Some(
            "source instruction accounts unavailable for Rust hot sniper".to_string(),
        ));
    };
    let Some(source_ix_data_base64) = line.source_ix_data_base64.clone() else {
        return HotDecision::Fallback(Some(
            "source instruction data unavailable for Rust hot sniper".to_string(),
        ));
    };
    if let Some(reason) = hot_market_cap_block_reason(hot, line, &source_ix_accounts).await {
        return HotDecision::Fallback(Some(reason));
    }
    let Some(sol_in_lamports) = hot_sized_lamports(hot, line.amount_raw) else {
        return HotDecision::Fallback(Some("copy-sized amount below minimum".to_string()));
    };
    if sol_in_lamports == 0 {
        return HotDecision::Fallback(Some("copy-sized amount is zero".to_string()));
    }

    // Reserve this source signature before broadcasting. ShredStream can surface the
    // same tx multiple times (extra entries, FEC recovery, reconnects); without this
    // every re-sighting would build and broadcast another real buy for one Omego tx.
    // Enforced regardless of BUY_ONCE_PER_TOKEN so DCA scale-ins (distinct signatures)
    // are still each mirrored exactly once.
    match hot.bought_signatures.lock() {
        Ok(mut seen) => {
            if seen.contains(&line.signature) {
                return HotDecision::Fallback(None);
            }
            seen.insert(line.signature.clone());
        }
        Err(_) => {
            return HotDecision::Fallback(Some("hot signature state lock poisoned".to_string()));
        }
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
        if let Ok(mut seen) = hot.bought_signatures.lock() {
            seen.remove(&line.signature);
        }
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
            release_hot_reservations(hot, &line.signature, &line.output_mint, sol_in_lamports);
            return HotDecision::Fallback(Some(format!("blockhash fetch failed: {err}")));
        }
    };
    let (priority_fee_micro_lamports, jito_tip_lamports, helius_tip_lamports) =
        capped_hot_fees(hot, sol_in_lamports);

    let command = executor::ExecutePumpfunBuyCommand {
        id: format!(
            "rust-hot-{}-{}",
            now_ms(),
            line.signature.chars().take(8).collect::<String>()
        ),
        mode: "source-replay".to_string(),
        route: "pumpfun-hot-source-replay-buy".to_string(),
        mint: line.output_mint.clone(),
        // The hot path already reserved this signature in bought_signatures and calls
        // the executor directly (bypassing the stdin gate), so this is informational.
        source_signature: Some(line.signature.clone()),
        quote_mint: Some(line.input_mint.clone()),
        sol_in_lamports,
        tokens_out_raw: None,
        min_tokens_out_raw: None,
        slippage_bps: hot.slippage_bps,
        buy_output_policy: None,
        exact_sol_in: hot.exact_sol_in,
        source_ix_accounts: Some(source_ix_accounts),
        source_ix_data_base64: Some(source_ix_data_base64),
        bonding_curve: None,
        creator: None,
        token_program_id: None,
        gate_virtual_sol_reserves: None,
        gate_virtual_token_reserves: None,
        gate_reserve_slot: None,
        gate_reserve_observed_at_ms: None,
        native_entry_drift_gate: false,
        shadow_entry_market_cap_usd: None,
        shadow_tokens_out_raw: None,
        shadow_sol_in_lamports: None,
        max_entry_drift_bps: None,
        landing_buffer_bps: None,
        max_entry_market_cap_usd: None,
        blockhash,
        compute_unit_limit: hot.compute_unit_limit,
        priority_fee_micro_lamports,
        jito_tip_lamports,
        helius_tip_lamports,
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
                release_hot_reservations(hot, &line.signature, &line.output_mint, sol_in_lamports);
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
            release_hot_reservations(hot, &line.signature, &line.output_mint, sol_in_lamports);
            HotDecision::Fallback(Some(
                response
                    .error
                    .unwrap_or_else(|| "Rust executor failed".to_string()),
            ))
        }
        Err(err) => {
            release_hot_reservations(hot, &line.signature, &line.output_mint, sol_in_lamports);
            HotDecision::Fallback(Some(format!("{err:#}")))
        }
    }
}

fn hot_sized_lamports(hot: &HotSniperConfig, source_lamports: u64) -> Option<u64> {
    let copied = if hot.copy_buy_percentage <= 0.0 {
        hot.min_buy_lamports
    } else {
        ((source_lamports as f64) * (hot.copy_buy_percentage / 100.0)).floor() as u64
    };
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

async fn hot_market_cap_block_reason(
    hot: &HotSniperConfig,
    line: &DetectedSwapLine,
    source_ix_accounts: &[String],
) -> Option<String> {
    if !hot.market_cap_filter_enabled {
        return None;
    }
    if hot.min_market_cap_usd.is_none()
        && hot.max_market_cap_usd.is_none()
        && hot.min_market_cap_sol.is_none()
        && hot.max_market_cap_sol.is_none()
    {
        return None;
    }
    match hot_market_cap_decision(hot, line, source_ix_accounts).await {
        Ok(None) => None,
        Ok(Some(reason)) => Some(reason),
        Err(err) => {
            let reason = format!(
                "Rust hot sniper MC unavailable for {}...: {err} ({})",
                line.output_mint.chars().take(8).collect::<String>(),
                if hot.market_cap_filter_fail_open {
                    "fail-open"
                } else {
                    "fail-closed"
                }
            );
            if hot.market_cap_filter_fail_open {
                eprintln!("rust-sidecar: {reason}; allowing buy");
                None
            } else {
                Some(reason)
            }
        }
    }
}

async fn hot_market_cap_decision(
    hot: &HotSniperConfig,
    line: &DetectedSwapLine,
    source_ix_accounts: &[String],
) -> Result<Option<String>> {
    let bonding_curve = source_bonding_curve_for_buy(&line.output_mint, source_ix_accounts)
        .context("source buy missing bonding curve account")?;
    let account_data = fetch_account_data_base64(
        &hot.rpc_fallback_url,
        &bonding_curve,
        hot.market_cap_rpc_timeout_ms,
    )
    .await?;
    let state = decode_bonding_curve_state(&account_data)
        .context("bonding curve account is too short or malformed")?;
    let market_cap_sol = estimate_market_cap_sol(&state)
        .context("bonding curve reserves cannot produce market cap")?;
    let min_sol_from_usd = match (hot.min_market_cap_usd, hot.sol_price_usd) {
        (Some(min_usd), Some(price)) => Some(min_usd / price),
        _ => None,
    };
    let max_sol_from_usd = match (hot.max_market_cap_usd, hot.sol_price_usd) {
        (Some(max_usd), Some(price)) => Some(max_usd / price),
        _ => None,
    };
    if (hot.min_market_cap_usd.is_some() || hot.max_market_cap_usd.is_some())
        && hot.sol_price_usd.is_none()
        && hot.min_market_cap_sol.is_none()
        && hot.max_market_cap_sol.is_none()
    {
        bail!(
            "Rust hot sniper MC needs LOCAL_PUMPFUN_SOL_PRICE_USD/SOL_PRICE_USD for USD thresholds"
        );
    }

    let min_sol = match (hot.min_market_cap_sol, min_sol_from_usd) {
        (Some(a), Some(b)) => Some(a.max(b)),
        (Some(a), None) => Some(a),
        (None, Some(b)) => Some(b),
        (None, None) => None,
    };
    let max_sol = match (hot.max_market_cap_sol, max_sol_from_usd) {
        (Some(a), Some(b)) => Some(a.min(b)),
        (Some(a), None) => Some(a),
        (None, Some(b)) => Some(b),
        (None, None) => None,
    };
    let label = format_market_cap_label(market_cap_sol, hot.sol_price_usd);
    if let Some(min_sol) = min_sol {
        if market_cap_sol < min_sol {
            return Ok(Some(format!(
                "Rust hot sniper MC {label} < {}",
                format_min_max_label(min_sol, hot.min_market_cap_usd, hot.sol_price_usd)
            )));
        }
    }
    if let Some(max_sol) = max_sol {
        if market_cap_sol > max_sol {
            return Ok(Some(format!(
                "Rust hot sniper MC {label} > {}",
                format_min_max_label(max_sol, hot.max_market_cap_usd, hot.sol_price_usd)
            )));
        }
    }
    eprintln!("rust-sidecar: Rust hot sniper MC passed: {label}");
    Ok(None)
}

fn source_bonding_curve_for_buy(mint: &str, source_ix_accounts: &[String]) -> Option<String> {
    let is_compact_token2022 = source_ix_accounts.len() >= 14
        && source_ix_accounts.get(0).is_some_and(|v| v == mint)
        && source_ix_accounts
            .get(1)
            .is_some_and(|v| v == PUMP_FUN_PROGRAM_ID)
        && source_ix_accounts
            .get(13)
            .is_some_and(|v| v == TOKEN_2022_PROGRAM_ID);
    if is_compact_token2022 {
        return source_ix_accounts.get(7).cloned();
    }
    for start in 0..source_ix_accounts.len() {
        let Some(mint_account) = source_ix_accounts.get(start + 2) else {
            continue;
        };
        let Some(program_account) = source_ix_accounts.get(start + 11) else {
            continue;
        };
        if mint_account == mint && program_account == PUMP_FUN_PROGRAM_ID {
            return source_ix_accounts.get(start + 3).cloned();
        }
    }
    let is_v2 = source_ix_accounts.len() >= 27
        && source_ix_accounts.get(1).is_some_and(|v| v == mint)
        && source_ix_accounts
            .get(26)
            .is_some_and(|v| v == PUMP_FUN_PROGRAM_ID);
    if is_v2 {
        return source_ix_accounts.get(10).cloned();
    }
    let is_legacy = source_ix_accounts.len() >= 16
        && source_ix_accounts.get(2).is_some_and(|v| v == mint)
        && source_ix_accounts
            .get(11)
            .is_some_and(|v| v == PUMP_FUN_PROGRAM_ID);
    if is_legacy {
        return source_ix_accounts.get(3).cloned();
    }
    None
}

fn pumpfun_bonding_curve_pda(mint: &str) -> Option<String> {
    let mint = Pubkey::from_str(mint).ok()?;
    let program_id = Pubkey::from_str(PUMP_FUN_PROGRAM_ID).ok()?;
    let (bonding_curve, _) =
        Pubkey::find_program_address(&[b"bonding-curve", mint.as_ref()], &program_id);
    Some(bonding_curve.to_string())
}

async fn fetch_account_data_base64(
    rpc_url: &str,
    pubkey: &str,
    timeout_ms: u64,
) -> Result<Vec<u8>> {
    let body = json!({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getAccountInfo",
        "params": [pubkey, { "encoding": "base64", "commitment": "processed" }],
    });
    let response = reqwest::Client::new()
        .post(rpc_url)
        .timeout(Duration::from_millis(timeout_ms))
        .json(&body)
        .send()
        .await
        .context("MC account rpc send failed")?;
    let json = response
        .json::<JsonRpcResponse>()
        .await
        .context("MC account rpc response parse failed")?;
    if let Some(error) = json.error {
        let code = error.code.map(|c| format!(" code {c}")).unwrap_or_default();
        bail!(
            "{}{}",
            error
                .message
                .unwrap_or_else(|| "getAccountInfo json-rpc error".to_string()),
            code
        );
    }
    let data_base64 = json
        .result
        .and_then(|v| v.get("value").cloned())
        .and_then(|v| {
            let owner_ok = v
                .get("owner")
                .and_then(Value::as_str)
                .is_some_and(|owner| owner == PUMP_FUN_PROGRAM_ID);
            if !owner_ok {
                return None;
            }
            v.get("data")
                .and_then(Value::as_array)
                .and_then(|arr| arr.first())
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .context("getAccountInfo returned no Pump.fun account data")?;
    BASE64_STANDARD
        .decode(data_base64)
        .context("MC account data is not valid base64")
}

#[derive(Debug, Clone)]
struct CurveState {
    virtual_token_reserves: u64,
    virtual_sol_reserves: u64,
    token_total_supply: u64,
    complete: bool,
    creator: Option<String>,
}

fn decode_bonding_curve_state(data: &[u8]) -> Option<CurveState> {
    if data.len() < 49 {
        return None;
    }
    let creator = data.get(49..81).and_then(|bytes| {
        let arr: [u8; 32] = bytes.try_into().ok()?;
        Some(Pubkey::new_from_array(arr).to_string())
    });
    Some(CurveState {
        virtual_token_reserves: read_u64_le_slice(data, 8)?,
        virtual_sol_reserves: read_u64_le_slice(data, 16)?,
        token_total_supply: read_u64_le_slice(data, 40)?,
        complete: data.get(48).copied().unwrap_or(0) != 0,
        creator,
    })
}

fn decode_spl_token_account_balance(data: &[u8]) -> Option<(String, String, u64)> {
    if data.len() < 72 {
        return None;
    }
    let mint_bytes: [u8; 32] = data.get(0..32)?.try_into().ok()?;
    let owner_bytes: [u8; 32] = data.get(32..64)?.try_into().ok()?;
    Some((
        Pubkey::new_from_array(mint_bytes).to_string(),
        Pubkey::new_from_array(owner_bytes).to_string(),
        read_u64_le_slice(data, 64)?,
    ))
}

fn decode_spl_token_account_owner_amount_slice(data: &[u8]) -> Option<(String, u64)> {
    if data.len() != 40 {
        return None;
    }
    let owner_bytes: [u8; 32] = data.get(0..32)?.try_into().ok()?;
    Some((
        Pubkey::new_from_array(owner_bytes).to_string(),
        read_u64_le_slice(data, 32)?,
    ))
}

fn read_u64_le_slice(data: &[u8], offset: usize) -> Option<u64> {
    let bytes = data.get(offset..offset + 8)?;
    let mut arr = [0u8; 8];
    arr.copy_from_slice(bytes);
    Some(u64::from_le_bytes(arr))
}

fn estimate_market_cap_sol(state: &CurveState) -> Option<f64> {
    let virtual_sol = state.virtual_sol_reserves as f64 / LAMPORTS_PER_SOL;
    let virtual_tokens = state.virtual_token_reserves as f64;
    let total_supply = state.token_total_supply as f64;
    if !virtual_sol.is_finite()
        || !virtual_tokens.is_finite()
        || !total_supply.is_finite()
        || virtual_sol <= 0.0
        || virtual_tokens <= 0.0
        || total_supply <= 0.0
    {
        return None;
    }
    let market_cap_sol = virtual_sol * (total_supply / virtual_tokens);
    market_cap_sol.is_finite().then_some(market_cap_sol)
}

fn quote_buy_tokens_out_raw_main(
    virtual_sol_reserves: u128,
    virtual_token_reserves: u128,
    sol_in_lamports: u128,
) -> Result<u128> {
    if virtual_sol_reserves == 0 || virtual_token_reserves == 0 || sol_in_lamports == 0 {
        bail!("candidate quote requires positive reserves and solInLamports");
    }
    let curve_sol = pumpfun_exact_sol_in_curve_lamports_main(sol_in_lamports)?;
    if curve_sol == 0 {
        bail!("candidate quote net sol after fee is zero");
    }
    let k = virtual_sol_reserves
        .checked_mul(virtual_token_reserves)
        .context("candidate quote reserve product overflow")?;
    let new_sol = virtual_sol_reserves
        .checked_add(curve_sol)
        .context("candidate quote sol reserve overflow")?;
    let new_tokens = k / new_sol;
    if new_tokens >= virtual_token_reserves {
        bail!("candidate quote produced zero tokens out");
    }
    Ok(virtual_token_reserves - new_tokens)
}

fn pumpfun_exact_sol_in_curve_lamports_main(sol_in_lamports: u128) -> Result<u128> {
    if sol_in_lamports == 0 {
        bail!("candidate quote requires positive solInLamports");
    }
    let total_fee_bps = parse_env_u64(
        "RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS",
        PUMP_FUN_TRADE_FEE_BPS as u64,
    )
    .min(2_000) as u128;
    let protocol_fee_bps = (PUMP_FUN_TRADE_FEE_BPS as u128).min(total_fee_bps);
    let creator_fee_bps = total_fee_bps.saturating_sub(protocol_fee_bps);
    let mut net_sol = sol_in_lamports
        .checked_mul(10_000u128)
        .context("candidate quote spend overflow")?
        .checked_div(10_000u128.saturating_add(total_fee_bps))
        .unwrap_or(0);
    let protocol_fee = ceil_div_u128(net_sol.saturating_mul(protocol_fee_bps), 10_000)?;
    let creator_fee = ceil_div_u128(net_sol.saturating_mul(creator_fee_bps), 10_000)?;
    let fee_adjustment = net_sol
        .checked_add(protocol_fee)
        .and_then(|value| value.checked_add(creator_fee))
        .context("candidate quote fee sum overflow")?
        .saturating_sub(sol_in_lamports);
    if fee_adjustment > 0 {
        net_sol = net_sol.saturating_sub(fee_adjustment);
    }
    Ok(net_sol.saturating_sub(1))
}

fn parse_iso_ms(_value: &str) -> Option<u64> {
    None
}

fn format_market_cap_label(market_cap_sol: f64, sol_price_usd: Option<f64>) -> String {
    if let Some(price) = sol_price_usd {
        format!(
            "{} ({})",
            format_usd(market_cap_sol * price),
            format_mc_sol(market_cap_sol)
        )
    } else {
        format_mc_sol(market_cap_sol)
    }
}

fn format_min_max_label(
    threshold_sol: f64,
    threshold_usd: Option<f64>,
    sol_price_usd: Option<f64>,
) -> String {
    if let Some(usd) = threshold_usd {
        format_usd(usd)
    } else if let Some(price) = sol_price_usd {
        format!(
            "{} ({})",
            format_usd(threshold_sol * price),
            format_mc_sol(threshold_sol)
        )
    } else {
        format_mc_sol(threshold_sol)
    }
}

fn format_mc_sol(value: f64) -> String {
    if value >= 100.0 {
        format!("{value:.1} SOL")
    } else {
        format!("{value:.2} SOL")
    }
}

fn format_usd(value: f64) -> String {
    if value >= 1_000_000.0 {
        format!("${:.2}M", value / 1_000_000.0)
    } else if value >= 1_000.0 {
        format!("${:.1}K", value / 1_000.0)
    } else {
        format!("${value:.0}")
    }
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

fn release_hot_reservations(
    hot: &HotSniperConfig,
    source_signature: &str,
    mint: &str,
    lamports: u64,
) {
    if let Ok(mut seen) = hot.bought_signatures.lock() {
        seen.remove(source_signature);
    }
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

fn build_request(tracked_wallets: &[String], bonding_curves: &[String]) -> SubscribeRequest {
    build_request_with_account_owner(tracked_wallets, bonding_curves, None)
}

fn build_request_with_account_owner(
    tracked_wallets: &[String],
    bonding_curves: &[String],
    account_owner: Option<&str>,
) -> SubscribeRequest {
    build_request_with_account_label(
        "rustExitCurves",
        tracked_wallets,
        bonding_curves,
        account_owner,
    )
}

fn build_selected_candidate_curve_cache_request(
    tracked_wallets: &[String],
    bonding_curves: &[String],
    owner_wide: bool,
) -> SubscribeRequest {
    let mut request = build_request_with_account_label(
        "rustSelectedCandidateWatchedCurves",
        tracked_wallets,
        bonding_curves,
        None,
    );
    if owner_wide {
        request.accounts.insert(
            "rustSelectedCandidateOwnerWideCurves".to_string(),
            SubscribeRequestFilterAccounts {
                account: vec![],
                owner: vec![PUMP_FUN_PROGRAM_ID.to_string()],
                filters: vec![],
                nonempty_txn_signature: None,
                cuckoo_accounts_filter: None,
            },
        );
    }
    request
}

fn build_selected_candidate_balance_cache_request(
    wallet: &str,
    atas: &[String],
) -> SubscribeRequest {
    let mut request = build_selected_candidate_wallet_balance_cache_request(wallet);
    add_selected_candidate_ata_balance_cache_filters(&mut request, atas);
    request
}

fn build_selected_candidate_balance_cache_request_for_watches(
    wallet: &str,
    watched: &[WatchedTokenAccountSubscription],
) -> SubscribeRequest {
    let mut request = build_selected_candidate_wallet_balance_cache_request(wallet);
    let atas = watched
        .iter()
        .map(|account| account.ata.clone())
        .collect::<Vec<_>>();
    add_selected_candidate_ata_balance_cache_filters(&mut request, &atas);
    add_selected_candidate_mint_owner_balance_cache_filters(&mut request, wallet, watched);
    if !watched.is_empty()
        && !parse_env_bool(
            "RUST_SELECTED_CANDIDATE_BALANCE_CACHE_FULL_DATA_DIAGNOSTIC",
            false,
        )
    {
        request.accounts_data_slice = vec![SubscribeRequestAccountsDataSlice {
            offset: 32,
            length: 40,
        }];
    }
    request
}

fn build_selected_candidate_direct_ata_balance_probe_request(
    wallet: &str,
    candidate: &SelectedCandidate,
    ata: &str,
) -> SubscribeRequest {
    let token_program = candidate
        .token_program_id
        .as_deref()
        .unwrap_or(TOKEN_2022_PROGRAM_ID);
    let mut request = SubscribeRequest {
        accounts: HashMap::new(),
        slots: HashMap::new(),
        transactions: HashMap::new(),
        transactions_status: HashMap::new(),
        blocks: HashMap::new(),
        blocks_meta: HashMap::new(),
        entry: HashMap::new(),
        accounts_data_slice: vec![],
        commitment: Some(CommitmentLevel::Processed as i32),
        ping: None,
        from_slot: None,
    };
    request.accounts.insert(
        "rustSelectedCandidateDirectAtaBalance".to_string(),
        SubscribeRequestFilterAccounts {
            account: vec![ata.to_string()],
            owner: vec![],
            filters: vec![],
            nonempty_txn_signature: None,
            cuckoo_accounts_filter: None,
        },
    );
    request.accounts.insert(
        "rustSelectedCandidateDirectMintOwnerBalance".to_string(),
        SubscribeRequestFilterAccounts {
            account: vec![],
            owner: vec![token_program.to_string()],
            filters: selected_candidate_mint_owner_balance_filters(
                token_program,
                &candidate.mint,
                wallet,
            ),
            nonempty_txn_signature: None,
            cuckoo_accounts_filter: None,
        },
    );
    request.transactions.insert(
        "rustSelectedCandidateDirectAtaBalanceTxs".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            signature: None,
            account_include: vec![ata.to_string()],
            account_exclude: vec![],
            account_required: vec![],
            token_accounts: selected_candidate_balance_changed_token_accounts(),
        },
    );
    request
}

fn build_selected_candidate_wallet_balance_cache_request(wallet: &str) -> SubscribeRequest {
    let owner_memcmp = || SubscribeRequestFilterAccountsFilter {
        filter: Some(subscribe_request_filter_accounts_filter::Filter::Memcmp(
            SubscribeRequestFilterAccountsFilterMemcmp {
                offset: 32,
                data: Some(
                    subscribe_request_filter_accounts_filter_memcmp::Data::Base58(
                        wallet.to_string(),
                    ),
                ),
            },
        )),
    };
    let token_account_state = || SubscribeRequestFilterAccountsFilter {
        filter: Some(subscribe_request_filter_accounts_filter::Filter::TokenAccountState(true)),
    };
    let token_account_datasize = || SubscribeRequestFilterAccountsFilter {
        filter: Some(subscribe_request_filter_accounts_filter::Filter::Datasize(
            165,
        )),
    };
    let mut accounts = HashMap::new();
    accounts.insert(
        "rustSelectedCandidateWalletTokenBalances".to_string(),
        SubscribeRequestFilterAccounts {
            account: vec![],
            owner: vec![TOKEN_PROGRAM_ID.to_string()],
            filters: vec![
                token_account_state(),
                token_account_datasize(),
                owner_memcmp(),
            ],
            nonempty_txn_signature: None,
            cuckoo_accounts_filter: None,
        },
    );
    accounts.insert(
        "rustSelectedCandidateWalletToken2022Balances".to_string(),
        SubscribeRequestFilterAccounts {
            account: vec![],
            owner: vec![TOKEN_2022_PROGRAM_ID.to_string()],
            filters: vec![token_account_state(), owner_memcmp()],
            nonempty_txn_signature: None,
            cuckoo_accounts_filter: None,
        },
    );

    let mut transactions = HashMap::new();
    transactions.insert(
        "rustSelectedCandidateWalletBalanceTxs".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            signature: None,
            account_include: vec![wallet.to_string()],
            account_exclude: vec![],
            account_required: vec![],
            token_accounts: selected_candidate_balance_changed_token_accounts(),
        },
    );
    transactions.insert(
        "rustSelectedCandidateWalletTokenProgramTxs".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            signature: None,
            account_include: vec![TOKEN_PROGRAM_ID.to_string()],
            account_exclude: vec![],
            account_required: vec![wallet.to_string()],
            token_accounts: selected_candidate_balance_changed_token_accounts(),
        },
    );
    transactions.insert(
        "rustSelectedCandidateWalletToken2022ProgramTxs".to_string(),
        SubscribeRequestFilterTransactions {
            vote: Some(false),
            failed: Some(false),
            signature: None,
            account_include: vec![TOKEN_2022_PROGRAM_ID.to_string()],
            account_exclude: vec![],
            account_required: vec![wallet.to_string()],
            token_accounts: selected_candidate_balance_changed_token_accounts(),
        },
    );

    SubscribeRequest {
        accounts,
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

fn selected_candidate_token_account_state_filter() -> SubscribeRequestFilterAccountsFilter {
    SubscribeRequestFilterAccountsFilter {
        filter: Some(subscribe_request_filter_accounts_filter::Filter::TokenAccountState(true)),
    }
}

fn selected_candidate_token_account_datasize_filter() -> SubscribeRequestFilterAccountsFilter {
    SubscribeRequestFilterAccountsFilter {
        filter: Some(subscribe_request_filter_accounts_filter::Filter::Datasize(
            165,
        )),
    }
}

fn selected_candidate_mint_owner_balance_filters(
    token_program: &str,
    mint: &str,
    wallet: &str,
) -> Vec<SubscribeRequestFilterAccountsFilter> {
    let mut filters = vec![selected_candidate_token_account_state_filter()];
    if token_program == TOKEN_PROGRAM_ID {
        filters.push(selected_candidate_token_account_datasize_filter());
    }
    filters.push(selected_candidate_memcmp_base58_filter(0, mint));
    filters.push(selected_candidate_memcmp_base58_filter(32, wallet));
    filters
}

fn selected_candidate_balance_changed_token_accounts() -> Option<i32> {
    Some(TokenAccountExpansionControlFlag::BalanceChanged as i32)
}

fn selected_candidate_memcmp_base58_filter(
    offset: u64,
    value: &str,
) -> SubscribeRequestFilterAccountsFilter {
    SubscribeRequestFilterAccountsFilter {
        filter: Some(subscribe_request_filter_accounts_filter::Filter::Memcmp(
            SubscribeRequestFilterAccountsFilterMemcmp {
                offset,
                data: Some(
                    subscribe_request_filter_accounts_filter_memcmp::Data::Base58(
                        value.to_string(),
                    ),
                ),
            },
        )),
    }
}

fn build_selected_candidate_ata_balance_cache_request(atas: &[String]) -> SubscribeRequest {
    let mut request = SubscribeRequest {
        accounts: HashMap::new(),
        slots: HashMap::new(),
        transactions: HashMap::new(),
        transactions_status: HashMap::new(),
        blocks: HashMap::new(),
        blocks_meta: HashMap::new(),
        entry: HashMap::new(),
        accounts_data_slice: vec![],
        commitment: Some(CommitmentLevel::Processed as i32),
        ping: None,
        from_slot: None,
    };
    add_selected_candidate_ata_balance_cache_filters(&mut request, atas);
    request
}

fn add_selected_candidate_mint_owner_balance_cache_filters(
    request: &mut SubscribeRequest,
    wallet: &str,
    watched: &[WatchedTokenAccountSubscription],
) {
    for (index, account) in watched.iter().enumerate() {
        let token_program = if account.token_program_id == TOKEN_PROGRAM_ID {
            TOKEN_PROGRAM_ID
        } else if account.token_program_id == TOKEN_2022_PROGRAM_ID {
            TOKEN_2022_PROGRAM_ID
        } else {
            continue;
        };
        request.accounts.insert(
            format!("rustSelectedCandidateMintOwnerBalance{index}"),
            SubscribeRequestFilterAccounts {
                account: vec![],
                owner: vec![token_program.to_string()],
                filters: selected_candidate_mint_owner_balance_filters(
                    token_program,
                    &account.mint,
                    wallet,
                ),
                nonempty_txn_signature: None,
                cuckoo_accounts_filter: None,
            },
        );
    }
}

fn add_selected_candidate_ata_balance_cache_filters(
    request: &mut SubscribeRequest,
    atas: &[String],
) {
    if !atas.is_empty() {
        request.accounts.insert(
            "rustSelectedCandidateAtaBalances".to_string(),
            SubscribeRequestFilterAccounts {
                account: atas.to_vec(),
                owner: vec![],
                filters: vec![],
                nonempty_txn_signature: None,
                cuckoo_accounts_filter: None,
            },
        );
        // Shyft/Yellowstone account-only requests can look subscribed while
        // failing to deliver just-created ATA updates. Keep a narrow
        // transaction filter alongside the exact account filter, matching the
        // workaround used by the exit curve account stream.
        request.transactions.insert(
            "rustSelectedCandidateAtaBalanceTxs".to_string(),
            SubscribeRequestFilterTransactions {
                vote: Some(false),
                failed: Some(false),
                signature: None,
                account_include: atas.to_vec(),
                account_exclude: vec![],
                account_required: vec![],
                token_accounts: selected_candidate_balance_changed_token_accounts(),
            },
        );
    }
}

fn build_request_with_account_label(
    account_label: &str,
    tracked_wallets: &[String],
    accounts_filter: &[String],
    account_owner: Option<&str>,
) -> SubscribeRequest {
    let mut accounts = HashMap::new();
    if !accounts_filter.is_empty() || account_owner.is_some() {
        accounts.insert(
            account_label.to_string(),
            SubscribeRequestFilterAccounts {
                account: accounts_filter.to_vec(),
                owner: account_owner
                    .map(|owner| vec![owner.to_string()])
                    .unwrap_or_default(),
                filters: vec![],
                nonempty_txn_signature: None,
                cuckoo_accounts_filter: None,
            },
        );
    }
    let mut transactions = HashMap::new();
    if !tracked_wallets.is_empty() {
        transactions.insert(
            "trackedWallets".to_string(),
            SubscribeRequestFilterTransactions {
                vote: Some(false),
                failed: Some(false),
                signature: None,
                account_include: tracked_wallets.to_vec(),
                account_exclude: vec![],
                account_required: vec![],
                token_accounts: None,
            },
        );
    }

    SubscribeRequest {
        accounts,
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
        let quote_mint = source_pumpfun_quote_mint(&source_ix_accounts, is_v2)?;
        if quote_mint != WRAPPED_SOL_MINT && quote_mint != USDC_MINT {
            continue;
        }
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
                input_mint: quote_mint.clone(),
                output_mint: mint,
                amount_raw: max_sol_cost,
                is_native_input: quote_mint == WRAPPED_SOL_MINT,
                counterparty_amount_raw: None,
                source_ix_accounts: Some(source_ix_accounts),
                source_ix_data_base64: Some(BASE64_STANDARD.encode(data)),
                source_pump_swap_instruction: None,
                source_pump_swap_accounts: None,
                source_quote_mint: Some(quote_mint.clone()),
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
                input_mint: quote_mint.clone(),
                output_mint: mint,
                amount_raw: spendable_sol,
                is_native_input: quote_mint == WRAPPED_SOL_MINT,
                counterparty_amount_raw: None,
                source_ix_accounts: Some(source_ix_accounts),
                source_ix_data_base64: Some(BASE64_STANDARD.encode(data)),
                source_pump_swap_instruction: None,
                source_pump_swap_accounts: None,
                source_quote_mint: Some(quote_mint.clone()),
                source_quote_amount_raw: Some(spendable_sol),
                source_slot,
                source_transaction_index,
            });
        }

        if starts_with(data, &SELL_DISCRIMINATOR) || starts_with(data, &SELL_V2_DISCRIMINATOR) {
            return pumpfun_sell_line_from_parts(
                data,
                signature,
                wallet,
                mint,
                "rust-sidecar",
                source_slot,
                source_transaction_index,
            );
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
        let quote_mint = source_pumpfun_quote_mint(&source_ix_accounts, is_v2)?;
        if quote_mint != WRAPPED_SOL_MINT && quote_mint != USDC_MINT {
            continue;
        }
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
                input_mint: quote_mint.clone(),
                output_mint: mint,
                amount_raw: max_sol_cost,
                is_native_input: quote_mint == WRAPPED_SOL_MINT,
                counterparty_amount_raw: None,
                source_ix_accounts: Some(source_ix_accounts),
                source_ix_data_base64: Some(BASE64_STANDARD.encode(data)),
                source_pump_swap_instruction: None,
                source_pump_swap_accounts: None,
                source_quote_mint: Some(quote_mint.clone()),
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
                input_mint: quote_mint.clone(),
                output_mint: mint,
                amount_raw: spendable_sol,
                is_native_input: quote_mint == WRAPPED_SOL_MINT,
                counterparty_amount_raw: None,
                source_ix_accounts: Some(source_ix_accounts),
                source_ix_data_base64: Some(BASE64_STANDARD.encode(data)),
                source_pump_swap_instruction: None,
                source_pump_swap_accounts: None,
                source_quote_mint: Some(quote_mint.clone()),
                source_quote_amount_raw: Some(spendable_sol),
                source_slot,
                source_transaction_index,
            });
        }

        if starts_with(data, &SELL_DISCRIMINATOR) || starts_with(data, &SELL_V2_DISCRIMINATOR) {
            return pumpfun_sell_line_from_parts(
                data,
                signature,
                wallet,
                mint,
                "jito-shredstream",
                source_slot,
                source_transaction_index,
            );
        }
    }

    None
}

#[cfg(feature = "jito-shredstream")]
fn extract_native_pumpfun_observations(
    tx: &VersionedTransaction,
    source_slot: u64,
    source_transaction_index: u64,
) -> Vec<NativePumpfunObservation> {
    let account_keys = tx
        .message
        .static_account_keys()
        .iter()
        .map(|key| key.to_string())
        .collect::<Vec<_>>();
    let Some(signature) = tx.signatures.first().map(|signature| signature.to_string()) else {
        return Vec::new();
    };
    let mut observations = Vec::new();
    for (ix_position, ix) in tx.message.instructions().iter().enumerate() {
        let Some(program_id) = account_keys.get(ix.program_id_index as usize) else {
            continue;
        };
        if program_id != PUMP_FUN_PROGRAM_ID {
            continue;
        }
        let source_ix_accounts = ix
            .accounts
            .iter()
            .filter_map(|idx| account_keys.get(*idx as usize).cloned())
            .collect::<Vec<_>>();
        let data = ix.data.as_slice();
        if starts_with(data, &CREATE_DISCRIMINATOR) || starts_with(data, &CREATE_V2_DISCRIMINATOR) {
            let Some(mint) = source_ix_accounts.first().cloned() else {
                continue;
            };
            let token_program_id = if starts_with(data, &CREATE_V2_DISCRIMINATOR)
                || source_ix_accounts
                    .iter()
                    .any(|account| account == TOKEN_2022_PROGRAM_ID)
            {
                TOKEN_2022_PROGRAM_ID.to_string()
            } else {
                TOKEN_PROGRAM_ID.to_string()
            };
            observations.push(NativePumpfunObservation::Creation {
                bonding_curve: pumpfun_bonding_curve_pda(&mint),
                mint,
                signature: signature.clone(),
                slot: source_slot,
                transaction_index: source_transaction_index,
                observed_at_ms: now_ms().min(u64::MAX as u128) as u64,
                token_program_id,
            });
            continue;
        }

        let is_legacy = source_ix_accounts.len() >= 16
            && source_ix_accounts.get(2).is_some()
            && source_ix_accounts
                .get(11)
                .is_some_and(|value| value == PUMP_FUN_PROGRAM_ID);
        let is_v2 = source_ix_accounts.len() >= 27
            && source_ix_accounts.get(1).is_some()
            && source_ix_accounts
                .get(26)
                .is_some_and(|value| value == PUMP_FUN_PROGRAM_ID);
        if !is_legacy && !is_v2 {
            continue;
        }
        let quote_mint = source_pumpfun_quote_mint(&source_ix_accounts, is_v2);
        if quote_mint.as_deref() != Some(WRAPPED_SOL_MINT) {
            continue;
        }
        let side = if starts_with(data, &BUY_DISCRIMINATOR)
            || starts_with(data, &BUY_EXACT_SOL_IN_DISCRIMINATOR)
            || starts_with(data, &BUY_V2_DISCRIMINATOR)
            || starts_with(data, &BUY_EXACT_QUOTE_IN_V2_DISCRIMINATOR)
        {
            NativeFlowSide::Buy
        } else if starts_with(data, &SELL_DISCRIMINATOR)
            || starts_with(data, &SELL_V2_DISCRIMINATOR)
        {
            NativeFlowSide::Sell
        } else {
            continue;
        };
        let quote_lamports = if side == NativeFlowSide::Buy {
            if starts_with(data, &BUY_DISCRIMINATOR) || starts_with(data, &BUY_V2_DISCRIMINATOR) {
                read_u64_le(data, 16)
            } else {
                read_u64_le(data, 8)
            }
        } else {
            None
        };
        let sell_token_amount_raw = if side == NativeFlowSide::Sell {
            read_u64_le(data, 8)
        } else {
            None
        };
        let sell_quote_lamports = None;
        if side == NativeFlowSide::Buy && quote_lamports.is_none_or(|value| value == 0) {
            continue;
        }
        if side == NativeFlowSide::Sell && sell_token_amount_raw.is_none_or(|value| value == 0) {
            continue;
        }
        let (wallet_idx, mint_idx) = if is_v2 { (13, 1) } else { (6, 2) };
        let Some(wallet) = source_ix_accounts.get(wallet_idx).cloned() else {
            continue;
        };
        let Some(mint) = source_ix_accounts.get(mint_idx).cloned() else {
            continue;
        };
        let Some(bonding_curve) = source_bonding_curve_for_buy(&mint, &source_ix_accounts) else {
            continue;
        };
        let token_program_id = if source_ix_accounts
            .iter()
            .any(|account| account == TOKEN_2022_PROGRAM_ID)
        {
            TOKEN_2022_PROGRAM_ID.to_string()
        } else {
            TOKEN_PROGRAM_ID.to_string()
        };
        observations.push(NativePumpfunObservation::Flow(NativeFlowEvent {
            signature: signature.clone(),
            wallet,
            side,
            quote_lamports,
            sell_quote_lamports,
            sell_token_amount_raw,
            slot: source_slot,
            transaction_index: source_transaction_index,
            instruction_index: ix_position.min(u32::MAX as usize) as u32,
            observed_at_ms: now_ms().min(u64::MAX as u128) as u64,
            mint,
            bonding_curve,
            token_program_id,
            source_ix_accounts,
            source_ix_data_base64: BASE64_STANDARD.encode(data),
        }));
    }
    observations
}

fn source_pumpfun_quote_mint(source_ix_accounts: &[String], is_v2: bool) -> Option<String> {
    if is_v2 {
        source_ix_accounts.get(2).cloned()
    } else {
        Some(WRAPPED_SOL_MINT.to_string())
    }
}

fn pumpfun_sell_line_from_parts(
    data: &[u8],
    signature: String,
    wallet: String,
    mint: String,
    source: &'static str,
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
) -> Option<DetectedSwapLine> {
    if !starts_with(data, &SELL_DISCRIMINATOR) && !starts_with(data, &SELL_V2_DISCRIMINATOR) {
        return None;
    }
    let token_amount = read_u64_le(data, 8)?;
    if token_amount == 0 {
        return None;
    }
    Some(DetectedSwapLine {
        kind: "swap",
        source,
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
        source_pump_swap_instruction: None,
        source_pump_swap_accounts: None,
        source_quote_mint: None,
        source_quote_amount_raw: None,
        source_slot,
        source_transaction_index,
    })
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
        source_pump_swap_instruction: Some(if is_exact_quote_in {
            "buy_exact_quote_in"
        } else {
            "buy"
        }),
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

#[cfg(test)]
mod tests {
    use super::*;

    static PRIVATE_KEY_ENV_LOCK: Mutex<()> = Mutex::new(());

    fn set_test_private_key(wallet: &Keypair) -> std::sync::MutexGuard<'static, ()> {
        let guard = PRIVATE_KEY_ENV_LOCK
            .lock()
            .expect("test private key env lock poisoned");
        env::set_var(
            "PRIVATE_KEY_BASE58",
            bs58::encode(wallet.to_bytes()).into_string(),
        );
        guard
    }

    fn test_hot_sniper_config(copy_buy_percentage: f64) -> HotSniperConfig {
        HotSniperConfig {
            enabled: true,
            buy_once_per_token: true,
            bought_mints: Arc::new(Mutex::new(HashSet::new())),
            bought_signatures: Arc::new(Mutex::new(HashSet::new())),
            session_deployed_lamports: Arc::new(AtomicU64::new(0)),
            blockhash_cache: Arc::new(Mutex::new(None)),
            max_session_deployed_lamports: None,
            min_market_cap_usd: None,
            max_market_cap_usd: None,
            min_market_cap_sol: None,
            max_market_cap_sol: None,
            sol_price_usd: None,
            market_cap_filter_enabled: false,
            market_cap_filter_fail_open: false,
            market_cap_rpc_timeout_ms: 80,
            min_buy_lamports: 10_000_000,
            max_buy_lamports: 10_000_000,
            max_sol_per_token_lamports: None,
            copy_buy_percentage,
            min_buy_behavior: "skip".to_string(),
            slippage_bps: 5_000,
            exact_sol_in: true,
            compute_unit_limit: 1_400_000,
            priority_fee_micro_lamports: 0,
            jito_tip_lamports: 0,
            helius_tip_lamports: 0,
            max_execution_fee_lamports: None,
            max_execution_fee_pct_of_buy: None,
            use_jito_direct: false,
            use_jito_fallback: false,
            sender_rpc_fallback_enabled: false,
            execution_multi_send_enabled: false,
            jito_send_mode: "rpc".to_string(),
            jito_transaction_urls: Vec::new(),
            jito_bundle_urls: Vec::new(),
            sender_endpoint_url: String::new(),
            rpc_fallback_url: String::new(),
            jito_auth_uuid: None,
            blockhash_refresh_ms: 150,
            blockhash_max_age_ms: 800,
        }
    }

    fn test_exit_action(reason: exit_watcher::ExitReason) -> exit_watcher::ExitAction {
        exit_watcher::ExitAction {
            mint: "mint".to_string(),
            source_wallet: "source-wallet".to_string(),
            bonding_curve: "curve".to_string(),
            sequence: 1,
            reason,
            phase_before: exit_watcher::ExitPhase::Open,
            phase_after_success: exit_watcher::ExitPhase::Closed,
            tokens_in_raw: 1_000,
            expected_sol_out_lamports: 1_000_000,
            min_sol_out_lamports: 850_000,
            cost_basis_lamports: 900_000,
            decision_execution_fee_lamports: 0,
            pnl_pct: 12.0,
            gross_pnl_pct: 12.0,
            fee_adjusted_pnl_pct: 12.0,
            min_acceptable_take_profit_pnl_pct: None,
            min_acceptable_take_profit_sol_out_lamports: None,
            normal_take_profit_capture_slippage_bps: Some(500),
            tp_6003_salvage_min_realized_pnl_pct: Some(5.0),
            trigger_mark_source: "test",
            trigger_mark_slot: 1,
            trigger_mark_observed_at_ms: 1_000,
            trigger_mark_pnl_pct: 12.0,
            hard_stop_threshold_pnl_pct: (reason == exit_watcher::ExitReason::HardStopLoss)
                .then_some(-20.0),
            min_sol_out_mode: "quote_slippage",
            exact_balance_proof: true,
            safe_sell_tokens_raw: None,
            shadow: false,
            sell: exit_watcher::PumpfunSellTemplate {
                sell_v2: true,
                accounts: Vec::new(),
                compute_unit_limit: 160_000,
                priority_fee_micro_lamports: 0,
                jito_tip_lamports: 0,
                helius_tip_lamports: 0,
                use_jito_direct: false,
                use_jito_fallback: false,
                sender_rpc_fallback_enabled: false,
                execution_multi_send_enabled: false,
                jito_send_mode: "transaction".to_string(),
                jito_transaction_urls: Vec::new(),
                jito_bundle_urls: Vec::new(),
                sender_endpoint_url: String::new(),
                rpc_fallback_url: String::new(),
                jito_auth_uuid: None,
            },
        }
    }

    #[test]
    fn exit_min_out_retry_relaxation_keeps_first_take_profit_protected() {
        let action = test_exit_action(exit_watcher::ExitReason::TakeProfit);
        assert_eq!(
            effective_exit_min_sol_out_lamports(&action, 0, 5_000),
            (850_000, false, "protected")
        );
    }

    #[test]
    fn exit_min_out_retry_relaxation_only_affects_retry_profit_exits() {
        let action = test_exit_action(exit_watcher::ExitReason::TakeProfit);
        assert_eq!(
            effective_exit_min_sol_out_lamports(&action, 1, 5_000),
            (500_000, true, "retry_relaxed")
        );

        let hard_stop = test_exit_action(exit_watcher::ExitReason::HardStopLoss);
        assert_eq!(
            effective_exit_min_sol_out_lamports(&hard_stop, 1, 5_000),
            (850_000, false, "protected")
        );
    }

    #[test]
    fn pre_exact_profit_sell_can_use_first_attempt_landing_min_out() {
        let mut action = test_exit_action(exit_watcher::ExitReason::TakeProfit);
        action.exact_balance_proof = false;
        action.safe_sell_tokens_raw = Some(900);

        assert_eq!(
            effective_exit_min_sol_out_lamports_with_pre_exact_bps(&action, 0, 5_000, 2_500),
            (750_000, true, "pre_exact_profit_landing")
        );
    }

    #[test]
    fn pre_exact_safe_sell_amount_uses_lower_submitted_min_cap() {
        assert_eq!(
            selected_candidate_pre_exact_safe_sell_tokens_raw(10_000, 20_000, 9_800, 6_500),
            6_500
        );
    }

    #[test]
    fn pre_exact_safe_sell_amount_never_exceeds_quote_or_minimum_one() {
        assert_eq!(
            selected_candidate_pre_exact_safe_sell_tokens_raw(10_000, 5_000, 9_800, 10_000),
            5_000
        );
        assert_eq!(
            selected_candidate_pre_exact_safe_sell_tokens_raw(1, 1, 1, 1),
            1
        );
    }

    #[test]
    fn selected_candidate_token_program_hint_overrides_source_template_shape() {
        let token2022_source_accounts = vec![TOKEN_2022_PROGRAM_ID.to_string()];

        assert!(
            !selected_candidate_effective_token2022(
                "",
                Some(TOKEN_PROGRAM_ID),
                &token2022_source_accounts,
            ),
            "explicit legacy mint owner must not be overwritten by recovered Token-2022 source accounts"
        );
        assert!(
            selected_candidate_effective_token2022("", Some(TOKEN_2022_PROGRAM_ID), &[]),
            "explicit Token-2022 mint owner should select the v2 route"
        );
        assert!(
            selected_candidate_effective_token2022("", None, &token2022_source_accounts),
            "source account inference is only a fallback when no mint owner hint exists"
        );
    }

    #[test]
    fn pre_exact_landing_min_out_does_not_loosen_exact_or_loss_exits() {
        let exact_take_profit = test_exit_action(exit_watcher::ExitReason::TakeProfit);
        assert_eq!(
            effective_exit_min_sol_out_lamports_with_pre_exact_bps(
                &exact_take_profit,
                0,
                5_000,
                2_500
            ),
            (850_000, false, "protected")
        );

        let mut hard_stop = test_exit_action(exit_watcher::ExitReason::HardStopLoss);
        hard_stop.exact_balance_proof = false;
        hard_stop.safe_sell_tokens_raw = Some(900);
        assert_eq!(
            effective_exit_min_sol_out_lamports_with_pre_exact_bps(&hard_stop, 0, 5_000, 2_500),
            (850_000, false, "protected")
        );
    }

    #[test]
    fn exit_state_mismatch_finality_errors_trigger_immediate_wallet_flatten() {
        assert!(selected_candidate_exit_failure_should_flatten_immediately(
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":6023}]}"
        ));
        assert!(selected_candidate_exit_failure_should_flatten_immediately(
            "program error: Custom(6023)"
        ));
        assert!(selected_candidate_exit_failure_should_flatten_immediately(
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":3012}]}"
        ));
        assert!(selected_candidate_exit_failure_should_flatten_immediately(
            "program error: Custom(3012)"
        ));
        assert!(selected_candidate_exit_failure_should_flatten_immediately(
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":6074}]}"
        ));
        assert!(selected_candidate_exit_failure_should_flatten_immediately(
            "program error: Custom(6074)"
        ));
        assert!(!selected_candidate_exit_failure_should_flatten_immediately(
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":6042}]}"
        ));
    }

    #[test]
    fn buy_custom_6042_is_protected_missed_fill_not_strategy_loss() {
        let kind = selected_candidate_buy_finality_error_kind(Some(
            "{\"InstructionError\":[3,{\"Custom\":6042}]}",
        ));
        assert_eq!(kind, "buy_finality_custom_6042");
        assert_eq!(
            selected_candidate_buy_failure_accounting_bucket(kind),
            "protected_missed_fill"
        );

        let spaced_kind = selected_candidate_buy_finality_error_kind(Some(
            "{\"InstructionError\":[3,{\"Custom\": 6042}]}",
        ));
        assert_eq!(spaced_kind, "buy_finality_custom_6042");
        assert_eq!(
            selected_candidate_buy_failure_accounting_bucket(spaced_kind),
            "protected_missed_fill"
        );
    }

    #[test]
    fn non_6042_buy_finality_failure_is_failed_buy_no_position() {
        let kind = selected_candidate_buy_finality_error_kind(Some(
            "{\"InstructionError\":[3,{\"Custom\":3012}]}",
        ));
        assert_eq!(kind, "buy_finality_error");
        assert_eq!(
            selected_candidate_buy_failure_accounting_bucket(kind),
            "failed_buy_no_position"
        );
    }

    #[test]
    fn pumpfun_exact_sol_in_curve_lamports_uses_split_fee_ceiling_adjustment() {
        let _env_guard = PRIVATE_KEY_ENV_LOCK.lock().expect("test env lock poisoned");
        env::set_var("RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS", "250");
        let spend = 1_000_000_u128;
        let shortcut_net = spend * 10_000 / 10_250;
        let protocol_fee = ceil_div_u128(shortcut_net * 125, 10_000).unwrap();
        let creator_fee = ceil_div_u128(shortcut_net * 125, 10_000).unwrap();
        let adjustment = shortcut_net
            .checked_add(protocol_fee)
            .and_then(|value| value.checked_add(creator_fee))
            .unwrap()
            .saturating_sub(spend);
        let expected_curve_lamports = shortcut_net.saturating_sub(adjustment).saturating_sub(1);
        assert_eq!(
            pumpfun_exact_sol_in_curve_lamports_main(spend).unwrap(),
            expected_curve_lamports
        );
        assert!(
            adjustment > 0,
            "test should cover the IDL's net-sol adjustment branch"
        );
        env::remove_var("RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS");
    }

    #[test]
    fn native_buy_flow_replay_uses_same_curve_lamports_as_quote() {
        let _env_guard = PRIVATE_KEY_ENV_LOCK.lock().expect("test env lock poisoned");
        env::set_var("RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS", "250");
        let mut state = CurveState {
            virtual_token_reserves: 1_000_000_000_000_000,
            virtual_sol_reserves: 30_000_000_000,
            token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
            complete: false,
            creator: None,
        };
        let before_sol = state.virtual_sol_reserves as u128;
        let before_tokens = state.virtual_token_reserves as u128;
        let spend = 1_000_000_u64;
        let expected_curve_lamports =
            pumpfun_exact_sol_in_curve_lamports_main(spend as u128).unwrap();
        let expected_tokens_out =
            quote_buy_tokens_out_raw_main(before_sol, before_tokens, spend as u128).unwrap();
        let flow = NativeFlowEvent {
            signature: "test-buy-signature".to_string(),
            wallet: "test-wallet".to_string(),
            side: NativeFlowSide::Buy,
            quote_lamports: Some(spend),
            sell_quote_lamports: None,
            sell_token_amount_raw: None,
            slot: 1,
            transaction_index: 1,
            instruction_index: 1,
            observed_at_ms: now_ms().min(u64::MAX as u128) as u64,
            mint: "test-mint".to_string(),
            bonding_curve: "test-curve".to_string(),
            token_program_id: TOKEN_2022_PROGRAM_ID.to_string(),
            source_ix_accounts: Vec::new(),
            source_ix_data_base64: String::new(),
        };
        assert!(apply_native_flow_to_curve_state(&mut state, &flow).is_some());
        assert_eq!(
            state.virtual_sol_reserves as u128,
            before_sol + expected_curve_lamports
        );
        assert_eq!(
            state.virtual_token_reserves as u128,
            before_tokens - expected_tokens_out
        );
        env::remove_var("RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS");
    }

    #[test]
    fn recovered_selected_events_count_as_independent_curve_source() {
        assert!(selected_candidate_independent_curve_source(
            "selected-event-recovered-native"
        ));
        assert!(selected_candidate_independent_curve_source(
            "yellowstone-cache"
        ));
        assert!(
            !selected_candidate_independent_curve_source("reserve-observation-file"),
            "reserve observations can seed research/cache state, but are not executable final-guard state"
        );
        assert!(!selected_candidate_independent_curve_source(
            "selected-event"
        ));
    }

    #[test]
    fn hard_stop_authority_excludes_reserve_observation_file() {
        assert!(selected_candidate_hard_stop_authoritative_curve_source(
            "yellowstone-cache"
        ));
        assert!(selected_candidate_hard_stop_authoritative_curve_source(
            "native-shred-feature"
        ));
        assert!(
            !selected_candidate_hard_stop_authoritative_curve_source("reserve-observation-file"),
            "reserve observations may trigger revalidation, but cannot authorize a hard stop"
        );
        assert!(!selected_candidate_hard_stop_authoritative_curve_source(
            "rpc-stale-fallback"
        ));
    }

    #[test]
    fn expected_output_drop_bps_measures_deterioration_only() {
        assert_eq!(
            selected_candidate_expected_output_drop_bps(10_000, 10_000),
            Some(0)
        );
        assert_eq!(
            selected_candidate_expected_output_drop_bps(10_000, 12_000),
            Some(0)
        );
        assert_eq!(
            selected_candidate_expected_output_drop_bps(10_000, 7_500),
            Some(2_500)
        );
        assert_eq!(selected_candidate_expected_output_drop_bps(0, 1), None);
    }

    #[test]
    fn parses_native_shredstream_replay_reserves_from_state_identity() {
        assert_eq!(
            parse_native_shredstream_replay_reserves(
                "native-shredstream-replay:mint:30123456789:998765432100000"
            ),
            Some((30_123_456_789, 998_765_432_100_000))
        );
        assert_eq!(
            parse_native_shredstream_replay_reserves("other:shape"),
            None
        );
    }

    #[test]
    fn parses_teacher_impulse_decision_input_as_reserve_observation() {
        let row = json!({
            "schema_version": "teacher-impulse-parity-v1",
            "canonical_state": {
                "state_identity": "native-shredstream-replay:test-mint:30200000000:1000000000000000",
                "source_slot": 12345,
                "observed_at_ms": 987654321
            },
            "extra_decision_fields": {
                "bondingCurve": "test-bonding-curve"
            }
        });

        assert_eq!(
            parse_selected_candidate_decision_input_reserve_observation(&row),
            Some((
                "test-bonding-curve".to_string(),
                30_200_000_000,
                1_000_000_000_000_000,
                12_345,
                987_654_321,
            ))
        );
    }

    #[test]
    fn profit_custom_6003_gets_one_protected_retry_before_flatten() {
        let action = test_exit_action(exit_watcher::ExitReason::TakeProfit);
        assert!(selected_candidate_should_retry_profit_6003(
            &action,
            1,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":6003}]}"
        ));
        assert!(!selected_candidate_should_retry_profit_6003(
            &action,
            2,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":6003}]}"
        ));

        let hard_stop = test_exit_action(exit_watcher::ExitReason::HardStopLoss);
        assert!(!selected_candidate_should_retry_profit_6003(
            &hard_stop,
            1,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":6003}]}"
        ));
    }

    #[test]
    fn profit_exit_min_out_helper_separates_normal_capture_from_salvage() {
        let mut action = test_exit_action(exit_watcher::ExitReason::TakeProfit);
        action.cost_basis_lamports = 1_000_000;
        action.decision_execution_fee_lamports = 1_000;
        action.expected_sol_out_lamports = 1_300_000;
        action.min_sol_out_lamports = 1_235_000;
        action.min_acceptable_take_profit_pnl_pct = None;
        action.min_acceptable_take_profit_sol_out_lamports = None;
        action.normal_take_profit_capture_slippage_bps = Some(500);
        action.tp_6003_salvage_min_realized_pnl_pct = Some(5.0);

        let (normal_min, normal_mode) =
            selected_candidate_profit_exit_min_sol_out_lamports(&action, 1_300_000, 500);
        assert_eq!(normal_mode, "normal_take_profit_capture");
        assert_eq!(normal_min, 1_235_000);
        assert!(selected_candidate_fee_adjusted_pnl_pct(normal_min, 1_000_000, 1_000) > 20.0);

        action.min_acceptable_take_profit_pnl_pct = Some(5.0);
        action.min_acceptable_take_profit_sol_out_lamports = Some(1_051_000);
        let (salvage_min, salvage_mode) =
            selected_candidate_profit_exit_min_sol_out_lamports(&action, 1_300_000, 500);
        assert_eq!(salvage_mode, "tp_6003_salvage_min_realized_pnl");
        assert_eq!(salvage_min, 1_051_000);
        assert!(normal_min > salvage_min);
    }

    #[test]
    fn pre_exact_guaranteed_sell_state_mismatch_does_not_retry_by_default() {
        let mut action = test_exit_action(exit_watcher::ExitReason::TakeProfit);
        action.exact_balance_proof = false;
        action.safe_sell_tokens_raw = Some(900);

        assert!(!selected_candidate_should_retry_pre_exact_state_mismatch(
            &action,
            1,
            3,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":3012}]}"
        ));
        assert!(!selected_candidate_should_retry_pre_exact_state_mismatch(
            &action,
            3,
            3,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":3012}]}"
        ));

        action.exact_balance_proof = true;
        assert!(!selected_candidate_should_retry_pre_exact_state_mismatch(
            &action,
            1,
            3,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":3012}]}"
        ));
    }

    #[test]
    fn pre_exact_profit_state_mismatch_recovery_is_narrow() {
        let mut action = test_exit_action(exit_watcher::ExitReason::TakeProfit);
        action.exact_balance_proof = false;
        action.safe_sell_tokens_raw = Some(900);

        assert!(selected_candidate_should_try_pre_exact_profit_state_mismatch_recovery(
            &action,
            1,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":3012}]}"
        ));
        assert!(!selected_candidate_should_try_pre_exact_profit_state_mismatch_recovery(
            &action,
            2,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":3012}]}"
        ));

        action.exact_balance_proof = true;
        assert!(!selected_candidate_should_try_pre_exact_profit_state_mismatch_recovery(
            &action,
            1,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":3012}]}"
        ));

        let mut hard_stop = test_exit_action(exit_watcher::ExitReason::HardStopLoss);
        hard_stop.exact_balance_proof = false;
        hard_stop.safe_sell_tokens_raw = Some(900);
        assert!(!selected_candidate_should_try_pre_exact_profit_state_mismatch_recovery(
            &hard_stop,
            1,
            "sell not finalized ok: status=finalized_error error={\"InstructionError\":[2,{\"Custom\":3012}]}"
        ));
    }

    #[test]
    fn hot_sized_lamports_uses_min_buy_for_fixed_size_mode() {
        let hot = test_hot_sniper_config(0.0);
        assert_eq!(hot_sized_lamports(&hot, 1_500_000), Some(10_000_000));
        assert_eq!(hot_sized_lamports(&hot, 100_000_000), Some(10_000_000));
    }

    #[test]
    fn subscription_request_targets_only_active_exit_curves() {
        let wallets = vec!["tracked-wallet".to_string()];
        let curves = vec!["curve-a".to_string(), "curve-b".to_string()];
        let request = build_request(&wallets, &curves);

        let transaction_filter = request.transactions.get("trackedWallets").unwrap();
        assert_eq!(transaction_filter.account_include, wallets);
        let curve_filter = request.accounts.get("rustExitCurves").unwrap();
        assert_eq!(curve_filter.account, curves);
        assert!(curve_filter.owner.is_empty());

        let without_curves = build_request(&wallets, &[]);
        assert!(!without_curves.accounts.contains_key("rustExitCurves"));
        assert!(without_curves.transactions.contains_key("trackedWallets"));

        let account_only = build_request(&wallets, &curves);
        let account_transaction_filter = account_only.transactions.get("trackedWallets").unwrap();
        assert_eq!(account_transaction_filter.account_include, wallets);
        assert!(account_only.accounts.contains_key("rustExitCurves"));
    }

    #[test]
    fn subscription_request_can_target_pumpfun_owner_wide_curve_cache() {
        let request = build_request_with_account_owner(&[], &[], Some(PUMP_FUN_PROGRAM_ID));

        let curve_filter = request.accounts.get("rustExitCurves").unwrap();
        assert!(curve_filter.account.is_empty());
        assert_eq!(curve_filter.owner, vec![PUMP_FUN_PROGRAM_ID.to_string()]);
        assert!(request.transactions.is_empty());
    }

    #[test]
    fn early_activated_exit_watch_survives_post_buy_finality() {
        assert_eq!(
            selected_candidate_post_finality_exit_action(true, false, false, false),
            SelectedCandidatePostFinalityExitAction::KeepAlreadyActivated
        );
        assert_eq!(
            selected_candidate_post_finality_exit_action(true, true, true, true),
            SelectedCandidatePostFinalityExitAction::KeepAlreadyActivated
        );
    }

    #[test]
    fn post_buy_finality_only_cancels_when_no_watch_is_active_or_activatable() {
        assert_eq!(
            selected_candidate_post_finality_exit_action(false, true, false, false),
            SelectedCandidatePostFinalityExitAction::Activate
        );
        assert_eq!(
            selected_candidate_post_finality_exit_action(false, false, true, false),
            SelectedCandidatePostFinalityExitAction::DeferAwaitProof
        );
        assert_eq!(
            selected_candidate_post_finality_exit_action(false, false, false, true),
            SelectedCandidatePostFinalityExitAction::DeferAwaitProof
        );
        assert_eq!(
            selected_candidate_post_finality_exit_action(false, false, false, false),
            SelectedCandidatePostFinalityExitAction::CancelMissingPosition
        );
    }

    #[test]
    fn selected_candidate_balance_cache_can_prove_wallet_tx_balance_by_mint() {
        let wallet = Keypair::new();
        let _private_key_env = set_test_private_key(&wallet);
        let mint = Keypair::new().pubkey().to_string();
        let cache = SelectedCandidateBalanceCache::default();
        let (ata, token_program_id) =
            selected_candidate_ata_for_mint(&mint, Some(TOKEN_2022_PROGRAM_ID)).unwrap();
        cache
            .update_wallet_owned(
                ata.clone(),
                mint.clone(),
                wallet.pubkey().to_string(),
                token_program_id.clone(),
                123_456,
                42,
                now_ms().min(u64::MAX as u128) as u64,
            )
            .unwrap();

        let candidate = SelectedCandidate {
            key: "test-selected-candidate".to_string(),
            mint: mint.clone(),
            source_signature: "test-signature".to_string(),
            source_wallet: wallet.pubkey().to_string(),
            entry_market_cap_usd: 2500.0,
            event_lag_ms: 0,
            bonding_curve: Keypair::new().pubkey().to_string(),
            creator: "test-creator".to_string(),
            token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
            quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
            virtual_sol_reserves: 30_000_000_000,
            virtual_token_reserves: 1_000_000_000_000_000,
            shadow_tokens_out_raw: 123_456,
            shadow_sol_in_lamports: 2_000_000,
            reserve_slot: Some(42),
            reserve_observed_at_ms: None,
            reserve_source: "test",
            native_features: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
        };

        let cached = cache.get_fresh_for_candidate(&candidate, 1_000).unwrap();
        assert_eq!(cached.ata, ata);
        assert_eq!(cached.mint, mint);
        assert_eq!(cached.token_program_id, token_program_id);
        assert_eq!(cached.amount_raw, 123_456);
        assert!(cached.exact);
        assert_eq!(cached.source, "yellowstone-wallet-owned");
    }

    #[test]
    fn selected_candidate_balance_cache_can_prove_watched_owner_amount_slice() {
        let wallet = Keypair::new();
        let _private_key_env = set_test_private_key(&wallet);
        let mint = Keypair::new().pubkey().to_string();
        let cache = SelectedCandidateBalanceCache::default();
        let candidate = SelectedCandidate {
            key: "test-selected-candidate".to_string(),
            mint: mint.clone(),
            source_signature: "test-signature".to_string(),
            source_wallet: wallet.pubkey().to_string(),
            entry_market_cap_usd: 2500.0,
            event_lag_ms: 0,
            bonding_curve: Keypair::new().pubkey().to_string(),
            creator: "test-creator".to_string(),
            token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
            quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
            virtual_sol_reserves: 30_000_000_000,
            virtual_token_reserves: 1_000_000_000_000_000,
            shadow_tokens_out_raw: 123_456,
            shadow_sol_in_lamports: 2_000_000,
            reserve_slot: Some(42),
            reserve_observed_at_ms: None,
            reserve_source: "test",
            native_features: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
        };
        let ata = cache.watch_candidate(&candidate).unwrap();
        let cached = cache
            .update_watched_owner_amount_slice(
                ata.clone(),
                TOKEN_2022_PROGRAM_ID.to_string(),
                wallet.pubkey().to_string(),
                123_456_789,
                77,
                now_ms().min(u64::MAX as u128) as u64,
            )
            .unwrap();

        assert_eq!(cached.ata, ata);
        assert_eq!(cached.mint, mint);
        assert_eq!(cached.token_program_id, TOKEN_2022_PROGRAM_ID);
        assert_eq!(cached.amount_raw, 123_456_789);
        assert!(cached.exact);
        assert_eq!(cached.source, "yellowstone-account-slice");
        assert!(cache
            .get_fresh_exact_for_candidate(&candidate, 1_000)
            .is_some());
    }

    #[test]
    fn selected_candidate_balance_cache_preserves_first_nonzero_exact_balance() {
        let wallet = Keypair::new();
        let _private_key_env = set_test_private_key(&wallet);
        let mint = Keypair::new().pubkey().to_string();
        let cache = SelectedCandidateBalanceCache::default();
        let (ata, token_program_id) =
            selected_candidate_ata_for_mint(&mint, Some(TOKEN_2022_PROGRAM_ID)).unwrap();
        let candidate = SelectedCandidate {
            key: "test-selected-candidate".to_string(),
            mint: mint.clone(),
            source_signature: "test-signature".to_string(),
            source_wallet: wallet.pubkey().to_string(),
            entry_market_cap_usd: 2500.0,
            event_lag_ms: 0,
            bonding_curve: Keypair::new().pubkey().to_string(),
            creator: "test-creator".to_string(),
            token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
            quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
            virtual_sol_reserves: 30_000_000_000,
            virtual_token_reserves: 1_000_000_000_000_000,
            shadow_tokens_out_raw: 123_456,
            shadow_sol_in_lamports: 2_000_000,
            reserve_slot: Some(42),
            reserve_observed_at_ms: None,
            reserve_source: "test",
            native_features: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
        };

        cache
            .update_wallet_owned(
                ata.clone(),
                mint.clone(),
                wallet.pubkey().to_string(),
                token_program_id.clone(),
                123_456,
                42,
                now_ms().min(u64::MAX as u128) as u64,
            )
            .unwrap();
        cache
            .update_wallet_owned(
                ata,
                mint.clone(),
                wallet.pubkey().to_string(),
                token_program_id,
                0,
                43,
                now_ms().min(u64::MAX as u128) as u64,
            )
            .unwrap();

        let current = cache.get_fresh_for_candidate(&candidate, 1_000).unwrap();
        assert_eq!(current.amount_raw, 0);
        assert!(current.exact);

        let proof = cache
            .get_fresh_exact_for_candidate(&candidate, 1_000)
            .unwrap();
        assert_eq!(proof.amount_raw, 123_456);
        assert!(proof.exact);
        assert_eq!(proof.slot, 42);
    }

    #[test]
    fn selected_candidate_balance_cache_commits_submitted_buy_signature_as_non_exact_proof() {
        let wallet = Keypair::new();
        let _private_key_env = set_test_private_key(&wallet);
        let mint = Keypair::new().pubkey().to_string();
        let cache = SelectedCandidateBalanceCache::default();
        let candidate = SelectedCandidate {
            key: "test-selected-candidate".to_string(),
            mint: mint.clone(),
            source_signature: "test-source-signature".to_string(),
            source_wallet: wallet.pubkey().to_string(),
            entry_market_cap_usd: 2500.0,
            event_lag_ms: 0,
            bonding_curve: Keypair::new().pubkey().to_string(),
            creator: "test-creator".to_string(),
            token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
            quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
            virtual_sol_reserves: 30_000_000_000,
            virtual_token_reserves: 1_000_000_000_000_000,
            shadow_tokens_out_raw: 123_456,
            shadow_sol_in_lamports: 2_000_000,
            reserve_slot: Some(42),
            reserve_observed_at_ms: None,
            reserve_source: "test",
            native_features: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
        };
        let signature = "submitted-buy-signature".to_string();
        cache
            .remember_submitted_buy(signature.clone(), &candidate, 777_000)
            .unwrap();
        let cached = cache
            .commit_submitted_buy_signature(&signature, 99, now_ms().min(u64::MAX as u128) as u64)
            .unwrap();
        assert_eq!(cached.mint, mint);
        assert_eq!(cached.amount_raw, 777_000);
        assert!(!cached.exact);
        assert_eq!(
            cached.source,
            "yellowstone-successful-submitted-buy-signature"
        );
        assert!(cache.get_fresh_for_candidate(&candidate, 1_000).is_some());
        assert!(cache
            .get_fresh_exact_for_candidate(&candidate, 1_000)
            .is_none());
    }

    #[test]
    fn selected_candidate_balance_cache_does_not_treat_shredstream_signature_as_balance_proof() {
        let wallet = Keypair::new();
        let _private_key_env = set_test_private_key(&wallet);
        env::set_var(
            "RUST_SELECTED_CANDIDATE_SIGNATURE_ONLY_BALANCE_PROOF_ENABLED",
            "false",
        );
        env::set_var(
            "RUST_SELECTED_CANDIDATE_UNSAFE_SIGNATURE_ONLY_BALANCE_PROOF_ENABLED",
            "false",
        );
        let mint = Keypair::new().pubkey().to_string();
        let cache = SelectedCandidateBalanceCache::default();
        let candidate = SelectedCandidate {
            key: "test-selected-candidate".to_string(),
            mint: mint.clone(),
            source_signature: "test-source-signature".to_string(),
            source_wallet: wallet.pubkey().to_string(),
            entry_market_cap_usd: 2500.0,
            event_lag_ms: 0,
            bonding_curve: Keypair::new().pubkey().to_string(),
            creator: "test-creator".to_string(),
            token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
            quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
            virtual_sol_reserves: 30_000_000_000,
            virtual_token_reserves: 1_000_000_000_000_000,
            shadow_tokens_out_raw: 123_456,
            shadow_sol_in_lamports: 2_000_000,
            reserve_slot: Some(42),
            reserve_observed_at_ms: None,
            reserve_source: "test",
            native_features: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
        };
        let signature = "submitted-buy-signature".to_string();
        cache
            .remember_submitted_buy(signature.clone(), &candidate, 777_000)
            .unwrap();
        assert!(cache
            .remember_shredstream_transaction_signature(
                &signature,
                99,
                now_ms().min(u64::MAX as u128) as u64
            )
            .is_none());
        assert!(cache.get_fresh_for_candidate(&candidate, 1_000).is_none());
        assert!(cache
            .get_fresh_exact_for_candidate(&candidate, 1_000)
            .is_none());
    }

    #[test]
    fn selected_candidate_balance_cache_commits_submitted_buy_post_token_balance_as_exact_proof() {
        let wallet = Keypair::new();
        let _private_key_env = set_test_private_key(&wallet);
        let mint = Keypair::new().pubkey().to_string();
        let cache = SelectedCandidateBalanceCache::default();
        let candidate = SelectedCandidate {
            key: "test-selected-candidate".to_string(),
            mint: mint.clone(),
            source_signature: "test-source-signature".to_string(),
            source_wallet: wallet.pubkey().to_string(),
            entry_market_cap_usd: 2500.0,
            event_lag_ms: 0,
            bonding_curve: Keypair::new().pubkey().to_string(),
            creator: "test-creator".to_string(),
            token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
            quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
            virtual_sol_reserves: 30_000_000_000,
            virtual_token_reserves: 1_000_000_000_000_000,
            shadow_tokens_out_raw: 123_456,
            shadow_sol_in_lamports: 2_000_000,
            reserve_slot: Some(42),
            reserve_observed_at_ms: None,
            reserve_source: "test",
            native_features: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
        };
        let signature = "submitted-buy-signature".to_string();
        cache
            .remember_submitted_buy(signature.clone(), &candidate, 777_000)
            .unwrap();
        let cached = cache
            .commit_submitted_buy_post_token_balance(
                &signature,
                &mint,
                &wallet.pubkey().to_string(),
                TOKEN_2022_PROGRAM_ID,
                765_432,
                100,
                now_ms().min(u64::MAX as u128) as u64,
            )
            .unwrap();

        assert_eq!(cached.mint, mint);
        assert_eq!(cached.amount_raw, 765_432);
        assert!(cached.exact);
        assert_eq!(
            cached.source,
            "yellowstone-submitted-buy-post-token-balance"
        );
        let proof = cache
            .get_fresh_exact_for_candidate(&candidate, 1_000)
            .unwrap();
        assert_eq!(proof.amount_raw, 765_432);
        assert_eq!(proof.source, "yellowstone-submitted-buy-post-token-balance");
        assert!(cache
            .commit_submitted_buy_signature(&signature, 101, now_ms().min(u64::MAX as u128) as u64)
            .is_none());
    }

    #[test]
    fn pumpfun_sell_decoder_covers_legacy_and_v2_shredstream_payloads() {
        for discriminator in [SELL_DISCRIMINATOR, SELL_V2_DISCRIMINATOR] {
            let mut data = vec![0_u8; 16];
            data[..8].copy_from_slice(&discriminator);
            data[8..16].copy_from_slice(&123_456_u64.to_le_bytes());
            let line = pumpfun_sell_line_from_parts(
                &data,
                "signature".to_string(),
                "wallet".to_string(),
                "mint".to_string(),
                "jito-shredstream",
                Some(42),
                Some(7),
            )
            .unwrap();
            assert_eq!(line.input_mint, "mint");
            assert_eq!(line.output_mint, WRAPPED_SOL_MINT);
            assert_eq!(line.amount_raw, 123_456);
            assert_eq!(line.source, "jito-shredstream");
        }
    }

    #[test]
    fn source_bonding_curve_parser_handles_embedded_pumpfun_buy_window() {
        let mint = "8Tgj8k9P9JJk3d8NPjmB1v7EnNkqFnZ6Tr4qJsWWpump";
        let accounts = vec![
            "C3W947Fka39oegXqdmGUrXErwAG2TAoAxdaavqHHsFCf",
            "C3W947Fka39oegXqdmGUrXErwAG2TAoAxdaavqHHsFCf",
            "86Vh4XGLW2b6nvWbRyDs4ScgMXbuvRCHT7WbUT3RFxKG",
            "11111111111111111111111111111111",
            "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",
            PUMP_FUN_PROGRAM_ID,
            "CWBizwsSYu7QFc3CWqZoPkBZu8jQPym7ufdGrwSSGYMU",
            "DRvFo4h54K8gLjT6jyTvaCdS93WWi7gFWSHrdCTf3XBu",
            "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf",
            "AVmoTthdrX6tKt4nDjco2D775W2YK3sDhxPcMmzUAmTY",
            mint,
            "461mai1ggbiYLj93ZdUGxjmR3eRcaUokifZtivdbM2B4",
            "FwULR3svcKHKxNgP7srAjPcf6ux86vRVaXKWmAQK1rZB",
            "2byeujsRb9zTMshAKLr64tDfiohH5V9MaXTPJ6UwFM4J",
            "C3W947Fka39oegXqdmGUrXErwAG2TAoAxdaavqHHsFCf",
            "11111111111111111111111111111111",
            TOKEN_2022_PROGRAM_ID,
            "35YGWUqhv2rKAX3ybd1oQAiSzgJUa3LpKJSUx8kaenwY",
            "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
            PUMP_FUN_PROGRAM_ID,
            "Hq2wp8uJ9jCPsYgNHex8RtqdvMPfVGoYwjvF1ATiwn2Y",
            "2R5tKHyMmhCp1waFLzg6dpJxbKT8sqaDa3NsvqEq7xzi",
            "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt",
            PUMP_FUN_FEE_PROGRAM_ID,
            "DUc1gN4nZNJ2Tu5Sd8KHyShV9kTaa4nfc9qRrnaAoXsc",
            "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD",
            "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",
            "3Tu1Y9aNveLFN4WTAwnAwXL6tbUp5MMe3RxyybG4jTAS",
            "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",
            "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",
            "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",
            "FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9",
        ]
        .into_iter()
        .map(str::to_string)
        .collect::<Vec<_>>();

        assert_eq!(
            source_bonding_curve_for_buy(mint, &accounts).as_deref(),
            Some("461mai1ggbiYLj93ZdUGxjmR3eRcaUokifZtivdbM2B4")
        );
    }

    #[test]
    fn source_bonding_curve_parser_handles_compact_token2022_pumpfun_buy() {
        let mint = "JxdA3hGYT6CeSZ2nbm6PZjnEsCdS2erye2ks3EQpump";
        let accounts = vec![
            mint,
            PUMP_FUN_PROGRAM_ID,
            "6iBNjmixxzzbapoCb4vYxN3ngiYR9Mo2AZUk3Ak98LvS",
            "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf",
            "11111111111111111111111111111111",
            "CRQgnmaavDqL85SuotZK83nQghdyGSbJJ5R6H6XpwS1H",
            "Hq2wp8uJ9jCPsYgNHex8RtqdvMPfVGoYwjvF1ATiwn2Y",
            "9aAHneoCek9e6fmXNEMCdWdbDfxqKySre8b1kqQP9B1X",
            PUMP_FUN_FEE_PROGRAM_ID,
            "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
            "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt",
            "AVmoTthdrX6tKt4nDjco2D775W2YK3sDhxPcMmzUAmTY",
            "62JZ7FkjhRuuf4T9jW5Jhe9z6eTFWmAqks7EbKuiuuTP",
            TOKEN_2022_PROGRAM_ID,
            "GMqr27fQieBUu8H7L1VqfYJisXHMcmDbEed44UG1MJuq",
            "999mLZzdWNu7tWkAevqxo7UqcnCGyyToAVUwnrQXaRA9",
            "5s1FxrRQNsPF1djDFm9VVeC9KXhcLS1uX4jo6s4pzcuC",
            "GXPFM2caqTtQYC2cJ5yJRi9VDkpsYZXzYdwYpGnLmtDL",
            "5YxQFdt3Tr9zJLvkFccqXVUwhdTWJQc1fFg2YPbxvxeD",
            "5eHhjP8JaYkz83CWwvGU2uMUXefd3AazWGx4gpcuEEYD",
            "9M4giFFMxmFGXtc3feFzRai56WbBqehoSeRE5GK7gf7",
        ]
        .into_iter()
        .map(str::to_string)
        .collect::<Vec<_>>();

        assert_eq!(
            source_bonding_curve_for_buy(mint, &accounts).as_deref(),
            Some("9aAHneoCek9e6fmXNEMCdWdbDfxqKySre8b1kqQP9B1X")
        );
    }

    #[test]
    fn native_strict_parity_uses_feature_mc_for_shadow_selected_rows() {
        let row = json!({
            "schemaVersion": "two-cq-continuous-shadow-event-1",
            "event": "candidate_selected",
            "mint": "8y27j1WfzEmVRQ17itTYjP9FwbbENjTcpEpidpuRpump",
            "entryMarketCapUsd": 2500.0,
            "features": {
                "mint": "8y27j1WfzEmVRQ17itTYjP9FwbbENjTcpEpidpuRpump",
                "tokenProgram": "token_2022",
                "entryMarketCapUsd": 5750.0,
                "netBuySol60": 4.0,
                "buyCount60": 5,
                "uniqueWallets60": 5,
                "sellPressure60": 0.0,
                "dominantBuyShare60": 0.25,
                "uniquePerBuy60": 1.0,
                "buyCount1000ms": 4,
                "interBuyMedianMs": 7,
                "firstBuyerQuoteShareBps": 2500,
                "creationAgeMs": 750,
                "observedAtMs": 123456789
            }
        });

        let (mint, candidate) = native_strict_parity_candidate_from_row(&row, 123456999).unwrap();

        assert_eq!(mint, "8y27j1WfzEmVRQ17itTYjP9FwbbENjTcpEpidpuRpump");
        assert_eq!(candidate.observed_at_ms, 123456789);
        assert_eq!(candidate.feature_market_cap_usd, 5750.0);
    }

    #[test]
    fn native_strict_parity_ignores_shadow_skipped_rows() {
        let row = json!({
            "schemaVersion": "two-cq-continuous-shadow-event-1",
            "event": "candidate_skipped_post_quote_gate",
            "mint": "82uyTWvYY4fpHX33im5kEKrF3yc7xMmN9rb7YMaGpump",
            "entryMarketCapUsd": 7233.457379,
            "features": {
                "mint": "82uyTWvYY4fpHX33im5kEKrF3yc7xMmN9rb7YMaGpump",
                "tokenProgram": "token_2022",
                "entryMarketCapUsd": 7233.457379,
                "netBuySol60": 8.0,
                "buyCount60": 5,
                "uniqueWallets60": 5,
                "sellPressure60": 0.0,
                "dominantBuyShare60": 0.25,
                "uniquePerBuy60": 1.0,
                "buyCount1000ms": 4,
                "interBuyMedianMs": 7,
                "firstBuyerQuoteShareBps": 2500,
                "creationAgeMs": 750,
                "observedAtMs": 123456789
            }
        });

        assert!(native_strict_parity_candidate_from_row(&row, 123456999).is_none());
    }

    #[test]
    fn native_strict_live_submit_requires_parity_source_not_comparison_only() {
        assert_eq!(
            native_strict_submit_parity_config_error(
                true,
                true,
                None,
                Some("/tmp/shadow/events.jsonl"),
                None
            ),
            Some("RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED=true with RUST_NATIVE_STRICT_SELECTOR_ENABLED=true cannot use only RUST_NATIVE_STRICT_SELECTOR_COMPARISON_CANDIDATE_SOURCE; set RUST_NATIVE_STRICT_SELECTOR_PARITY_CANDIDATE_SOURCE so shadow_miss candidates are fail-closed before buy")
        );
        assert_eq!(
            native_strict_submit_parity_config_error(
                true,
                true,
                Some("/tmp/shadow/events.jsonl"),
                Some("/tmp/shadow/events.jsonl"),
                None
            ),
            None
        );
        assert_eq!(
            native_strict_submit_parity_config_error(
                true,
                true,
                None,
                Some("/tmp/shadow/events.jsonl"),
                Some("/tmp/exact-shadow-feed.jsonl")
            ),
            None
        );
    }

    #[test]
    fn selected_candidate_ata_balance_cache_request_includes_transaction_filter() {
        let atas = vec!["EWT312ThCgvc4FnRJwhNc4Yyw9gY9VKNWsVLxhpDMF3H".to_string()];
        let request = build_selected_candidate_ata_balance_cache_request(&atas);

        let account_filter = request
            .accounts
            .get("rustSelectedCandidateAtaBalances")
            .expect("ATA account filter");
        assert_eq!(account_filter.account, atas);
        assert_eq!(account_filter.nonempty_txn_signature, None);

        let tx_filter = request
            .transactions
            .get("rustSelectedCandidateAtaBalanceTxs")
            .expect("ATA transaction filter");
        assert_eq!(tx_filter.account_include, atas);
        assert_eq!(tx_filter.vote, Some(false));
        assert_eq!(tx_filter.failed, Some(false));
    }

    #[test]
    fn selected_candidate_combined_balance_cache_request_preserves_wallet_filters_with_atas() {
        let wallet = "9xQeWvG816bUx9EPvKc8ZDFi4Te1DkUa2dH7H2y4kq9r";
        let atas = vec!["EWT312ThCgvc4FnRJwhNc4Yyw9gY9VKNWsVLxhpDMF3H".to_string()];
        let request = build_selected_candidate_balance_cache_request(wallet, &atas);

        assert!(request
            .accounts
            .contains_key("rustSelectedCandidateWalletTokenBalances"));
        assert!(request
            .accounts
            .contains_key("rustSelectedCandidateWalletToken2022Balances"));
        assert!(request
            .accounts
            .contains_key("rustSelectedCandidateAtaBalances"));
        assert!(request
            .transactions
            .contains_key("rustSelectedCandidateWalletBalanceTxs"));
        assert!(request
            .transactions
            .contains_key("rustSelectedCandidateWalletTokenProgramTxs"));
        assert!(request
            .transactions
            .contains_key("rustSelectedCandidateWalletToken2022ProgramTxs"));
        assert!(request
            .transactions
            .contains_key("rustSelectedCandidateAtaBalanceTxs"));
    }

    #[test]
    fn selected_candidate_combined_balance_cache_request_tracks_watched_mint_owner_accounts() {
        let wallet = "9xQeWvG816bUx9EPvKc8ZDFi4Te1DkUa2dH7H2y4kq9r";
        let mint = "So11111111111111111111111111111111111111112";
        let watched = vec![WatchedTokenAccountSubscription {
            ata: "EWT312ThCgvc4FnRJwhNc4Yyw9gY9VKNWsVLxhpDMF3H".to_string(),
            mint: mint.to_string(),
            token_program_id: TOKEN_2022_PROGRAM_ID.to_string(),
        }];
        let request = build_selected_candidate_balance_cache_request_for_watches(wallet, &watched);

        assert!(request
            .accounts
            .contains_key("rustSelectedCandidateWalletTokenBalances"));
        assert!(request
            .accounts
            .contains_key("rustSelectedCandidateWalletToken2022Balances"));
        assert!(request
            .accounts
            .contains_key("rustSelectedCandidateAtaBalances"));
        assert_eq!(request.accounts_data_slice.len(), 1);
        assert_eq!(request.accounts_data_slice[0].offset, 32);
        assert_eq!(request.accounts_data_slice[0].length, 40);

        let mint_owner_filter = request
            .accounts
            .get("rustSelectedCandidateMintOwnerBalance0")
            .expect("watched mint owner account filter");
        assert_eq!(
            mint_owner_filter.owner,
            vec![TOKEN_2022_PROGRAM_ID.to_string()]
        );
        assert!(mint_owner_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::TokenAccountState(true))
        )));
        assert!(!mint_owner_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::Datasize(
                165
            ))
        )));
        assert!(mint_owner_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::Memcmp(memcmp))
                if memcmp.offset == 0
                    && matches!(
                        memcmp.data.as_ref(),
                        Some(subscribe_request_filter_accounts_filter_memcmp::Data::Base58(value))
                            if value == mint
                    )
        )));
        assert!(mint_owner_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::Memcmp(memcmp))
                if memcmp.offset == 32
                    && matches!(
                        memcmp.data.as_ref(),
                        Some(subscribe_request_filter_accounts_filter_memcmp::Data::Base58(value))
                            if value == wallet
            )
        )));
    }

    #[test]
    fn selected_candidate_direct_ata_balance_probe_request_targets_exact_ata_and_mint_owner() {
        let wallet = "9xQeWvG816bUx9EPvKc8ZDFi4Te1DkUa2dH7H2y4kq9r";
        let mint = "So11111111111111111111111111111111111111112";
        let ata = "EWT312ThCgvc4FnRJwhNc4Yyw9gY9VKNWsVLxhpDMF3H";
        let candidate = SelectedCandidate {
            key: "test-selected-candidate".to_string(),
            mint: mint.to_string(),
            source_signature: "test-signature".to_string(),
            source_wallet: wallet.to_string(),
            entry_market_cap_usd: 2500.0,
            event_lag_ms: 0,
            bonding_curve: Keypair::new().pubkey().to_string(),
            creator: "test-creator".to_string(),
            token_program_id: Some(TOKEN_2022_PROGRAM_ID.to_string()),
            quote_mint: Some(WRAPPED_SOL_MINT.to_string()),
            virtual_sol_reserves: 30_000_000_000,
            virtual_token_reserves: 1_000_000_000_000_000,
            shadow_tokens_out_raw: 123_456,
            shadow_sol_in_lamports: 2_000_000,
            reserve_slot: Some(42),
            reserve_observed_at_ms: None,
            reserve_source: "test",
            native_features: None,
            source_ix_accounts: None,
            source_ix_data_base64: None,
        };
        let request =
            build_selected_candidate_direct_ata_balance_probe_request(wallet, &candidate, ata);

        let ata_filter = request
            .accounts
            .get("rustSelectedCandidateDirectAtaBalance")
            .expect("direct ATA account filter");
        assert_eq!(ata_filter.account, vec![ata.to_string()]);
        assert!(ata_filter.owner.is_empty());

        let mint_owner_filter = request
            .accounts
            .get("rustSelectedCandidateDirectMintOwnerBalance")
            .expect("direct mint owner account filter");
        assert_eq!(
            mint_owner_filter.owner,
            vec![TOKEN_2022_PROGRAM_ID.to_string()]
        );
        assert!(mint_owner_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::TokenAccountState(true))
        )));
        assert!(!mint_owner_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::Datasize(
                165
            ))
        )));
        assert!(mint_owner_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::Memcmp(memcmp))
                if memcmp.offset == 0
                    && matches!(
                        memcmp.data.as_ref(),
                        Some(subscribe_request_filter_accounts_filter_memcmp::Data::Base58(value))
                            if value == mint
                    )
        )));
        assert!(mint_owner_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::Memcmp(memcmp))
                if memcmp.offset == 32
                    && matches!(
                        memcmp.data.as_ref(),
                        Some(subscribe_request_filter_accounts_filter_memcmp::Data::Base58(value))
                            if value == wallet
                    )
        )));

        let tx_filter = request
            .transactions
            .get("rustSelectedCandidateDirectAtaBalanceTxs")
            .expect("direct ATA transaction filter");
        assert_eq!(tx_filter.account_include, vec![ata.to_string()]);
        assert_eq!(tx_filter.vote, Some(false));
        assert_eq!(tx_filter.failed, Some(false));
        assert_eq!(
            tx_filter.token_accounts,
            selected_candidate_balance_changed_token_accounts()
        );
        assert!(request.accounts_data_slice.is_empty());
    }

    #[test]
    fn selected_candidate_wallet_balance_cache_request_tracks_token_program_txs() {
        let wallet = "9xQeWvG816bUx9EPvKc8ZDFi4Te1DkUa2dH7H2y4kq9r";
        let request = build_selected_candidate_wallet_balance_cache_request(wallet);

        let token_account_filter = request
            .accounts
            .get("rustSelectedCandidateWalletTokenBalances")
            .expect("token account filter");
        assert_eq!(
            token_account_filter.owner,
            vec![TOKEN_PROGRAM_ID.to_string()]
        );
        assert_eq!(token_account_filter.nonempty_txn_signature, None);
        assert!(token_account_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::TokenAccountState(true))
        )));
        assert!(token_account_filter.filters.iter().any(|filter| matches!(
            filter.filter.as_ref(),
            Some(subscribe_request_filter_accounts_filter::Filter::Datasize(
                165
            ))
        )));

        let token_2022_account_filter = request
            .accounts
            .get("rustSelectedCandidateWalletToken2022Balances")
            .expect("token2022 account filter");
        assert_eq!(
            token_2022_account_filter.owner,
            vec![TOKEN_2022_PROGRAM_ID.to_string()]
        );
        assert!(token_2022_account_filter
            .filters
            .iter()
            .any(|filter| matches!(
                filter.filter.as_ref(),
                Some(subscribe_request_filter_accounts_filter::Filter::TokenAccountState(true))
            )));
        assert!(!token_2022_account_filter
            .filters
            .iter()
            .any(|filter| matches!(
                filter.filter.as_ref(),
                Some(subscribe_request_filter_accounts_filter::Filter::Datasize(
                    165
                ))
            )));

        let token_tx_filter = request
            .transactions
            .get("rustSelectedCandidateWalletTokenProgramTxs")
            .expect("token program tx filter");
        assert_eq!(
            token_tx_filter.account_include,
            vec![TOKEN_PROGRAM_ID.to_string()]
        );
        assert_eq!(token_tx_filter.account_required, vec![wallet.to_string()]);
        assert_eq!(token_tx_filter.vote, Some(false));
        assert_eq!(token_tx_filter.failed, Some(false));
        assert_eq!(
            token_tx_filter.token_accounts,
            selected_candidate_balance_changed_token_accounts()
        );

        let token_2022_tx_filter = request
            .transactions
            .get("rustSelectedCandidateWalletToken2022ProgramTxs")
            .expect("token2022 program tx filter");
        assert_eq!(
            token_2022_tx_filter.account_include,
            vec![TOKEN_2022_PROGRAM_ID.to_string()]
        );
        assert_eq!(
            token_2022_tx_filter.account_required,
            vec![wallet.to_string()]
        );
        assert_eq!(
            token_2022_tx_filter.token_accounts,
            selected_candidate_balance_changed_token_accounts()
        );
    }

    #[test]
    fn detects_pumpfun_v2_usdc_buy_quote_asset() {
        let wallet = "omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq";
        let mint = "q4dAfeqkkhMpKFs2aeAQpgvT5iA1sL38hdsUeGEpump";
        let accounts = vec![
            "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf",
            mint,
            USDC_MINT,
            TOKEN_2022_PROGRAM_ID,
            TOKEN_PROGRAM_ID,
            ASSOCIATED_TOKEN_PROGRAM_ID,
            "FWsW1xNtWscwNmKv6wVsU1iTzRN6wmmk3MjxRP5tT7hz",
            "2yC9PAQvtxFjdV2G79N7cGsFhitbNiEQmZ3Z6dmLWfQg",
            "3BpXnfJaUTiwXnJNe7Ej1rcbzqTTQUvLShZaWazebsVR",
            "9JR4rG7BK32TVENGAcKMseS7tdoz3Y5pXeSq234MEowH",
            "A69ypmTwsmj1et6xWEaz7YioLpFX4VX5zQDzyNEWL17",
            "9AoEshVnGddHjS9JfQjmwV7syA8bwky94TyEhDGmrk61",
            "8sRf3VcJtspQK2ySq39K8wsXtXx6wAPM4p3UKxmMbaWd",
            wallet,
            "FctN6nqtpVPJGKToEk6VHCDYhK8vZP9ehQKAgjL99hxn",
            "DEQQNgGZDtfBk9B1qpJR5GDGdH9RUfDzKNs9rrmP1fLH",
            "B1DDVTJFJvi1MsrEx49fFWP9ELH4sHd8rV8XeCWphyn1",
            "BCrPjRZ5yievZi57sAAXXkjE94o8VBeM6ZQMnEnrHunP",
            "AcKouGz7spajkDJseX4K2D31GDN5WjrTCnxjm6EZcnfM",
            "Hq2wp8uJ9jCPsYgNHex8RtqdvMPfVGoYwjvF1ATiwn2Y",
            "CKFkZtNhXUY1BTyNCAQq7NrfbHBTYLJ1GMAjvdkhRKBB",
            "FtY6z4VZxYxRRxHL6KjEo3rSKN6eKH5VDTzSSs1kw6Tc",
            "8Wf5TiAheLUqBrKXeYg2JtAFFMWtKdG2BSFgqUcPVwTt",
            PUMP_FUN_FEE_PROGRAM_ID,
            "11111111111111111111111111111111",
            "Ce6TQqeHC9p8KetsN6JsjHK7UTZk7nasjjnr7XxXp9F1",
            PUMP_FUN_PROGRAM_ID,
        ]
        .into_iter()
        .map(str::to_string)
        .collect::<Vec<_>>();
        let data = bs58::decode("HnQ14hy6fxGW2V5S6E5thtWdkf6VMdvko")
            .into_vec()
            .unwrap();
        assert_eq!(
            source_pumpfun_quote_mint(&accounts, true).as_deref(),
            Some(USDC_MINT)
        );
        assert_eq!(
            source_pumpfun_quote_mint(&accounts, false).as_deref(),
            Some(WRAPPED_SOL_MINT)
        );

        let token_amount = read_u64_le(&data, 8).unwrap();
        let max_quote_cost = read_u64_le(&data, 16).unwrap();
        assert_eq!(token_amount, 6_589_576_041_720);
        assert_eq!(max_quote_cost, 97_456_322);
        assert_eq!(&data[..8], &BUY_V2_DISCRIMINATOR);
        assert_eq!(accounts[2], USDC_MINT);
    }

    #[test]
    fn selected_candidate_curve_cache_requires_fresh_yellowstone_for_yellowstone_guard() {
        let cache = CandidateCurveCache::default();
        let bonding_curve = "curve111111111111111111111111111111111111111".to_string();
        let state = CurveState {
            virtual_token_reserves: 1_000_000_000,
            virtual_sol_reserves: 30_000_000_000,
            token_total_supply: PUMPFUN_TOKEN_TOTAL_SUPPLY,
            complete: false,
            creator: None,
        };
        let now = now_ms().min(u64::MAX as u128) as u64;

        cache.update_from_source(
            bonding_curve.clone(),
            state.clone(),
            1,
            now,
            "reserve-observation-file",
        );
        assert!(cache.get_fresh(&bonding_curve, 1_000).is_some());
        assert!(cache.get_fresh_yellowstone(&bonding_curve, 1_000).is_none());

        cache.update_from_source(
            bonding_curve.clone(),
            state.clone(),
            2,
            now.saturating_sub(1_000),
            "yellowstone-cache",
        );
        assert!(cache.get_fresh_yellowstone(&bonding_curve, 10).is_none());

        cache.update_from_source(bonding_curve.clone(), state, 3, now, "yellowstone-cache");
        let fresh = cache
            .get_fresh_yellowstone(&bonding_curve, 1_000)
            .expect("fresh Yellowstone state");
        assert_eq!(fresh.source, "yellowstone-cache");
        assert_eq!(fresh.slot, 3);
    }

    #[test]
    fn prebuy_balance_settle_is_not_before_curve_snapshot_on_hot_path() {
        let source = include_str!("main.rs");
        let submit_start = source
            .find("async fn submit_selected_candidate_buy")
            .expect("submit function exists");
        let submit = &source[submit_start..];
        let settle_event = submit
            .find("\"kind\": \"rustSelectedCandidateBalanceCachePrebuyWatchSettled\"")
            .expect("settle event exists");
        let curve_snapshot = submit
            .find("match selected_candidate_curve_snapshot")
            .expect("curve snapshot call exists");
        let before_curve_snapshot = &submit[..curve_snapshot];

        assert!(settle_event < curve_snapshot);
        assert!(before_curve_snapshot.contains("tokio::spawn(async move"));
        assert!(before_curve_snapshot.contains("\"nonBlocking\": true"));
        assert!(before_curve_snapshot.contains("\"criticalPathBlocked\": false"));
    }

    #[test]
    fn reservation_safety_still_precedes_nonblocking_balance_settle() {
        let source = include_str!("main.rs");
        let submit_start = source
            .find("async fn submit_selected_candidate_buy")
            .expect("submit function exists");
        let submit = &source[submit_start..];
        let reserve = submit
            .find("reserve_selected_candidate(cfg, hot, &candidate)")
            .expect("reserve call exists");
        let accepted = submit
            .find("\"kind\": \"rustSelectedCandidateAccepted\"")
            .expect("accepted event exists");
        let balance_watch = submit
            .find("\"kind\": \"rustSelectedCandidateBalanceCacheWatch\"")
            .expect("balance watch event exists");
        let settle_spawn = submit
            .find("tokio::spawn(async move")
            .expect("non-blocking settle spawn exists");
        let curve_snapshot = submit
            .find("match selected_candidate_curve_snapshot")
            .expect("curve snapshot call exists");

        assert!(reserve < accepted);
        assert!(accepted < balance_watch);
        assert!(balance_watch < settle_spawn);
        assert!(settle_spawn < curve_snapshot);
    }
}
