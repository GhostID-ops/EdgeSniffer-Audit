//! Ranker V2 production-translation no-submit builder.
//!
//! This binary imports the deployed Rust executor modules and exposes only
//! dry-build probes. It signs Pump.fun buy/sell transactions with an ephemeral
//! process-local keypair and has no request branch that can submit/broadcast.

#![recursion_limit = "256"]

#[path = "../executor.rs"]
mod executor;
#[path = "../exit_watcher.rs"]
mod exit_watcher;

use std::{
    env,
    sync::{Arc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use anyhow::{anyhow, bail, Context, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use solana_keypair::Keypair;
use solana_signer::Signer;
use tokio::{
    io::{self, AsyncBufReadExt, BufReader},
    time::sleep,
};

const REQUEST_KIND: &str = "buildRankerV2ProductionNoSubmit";
const EXPECTED_COHORT: &str = "RANKER_V2_PROSPECTIVE_SHADOW_V2";
const EXPECTED_SPEC_SHA256: &str =
    "ca785e8d919ccc795be2c41c9787ad5a77617767cd75f748a111f93eb00cacee";
const EXPECTED_OBJECT_SHA256: &str =
    "3784908b49f724ba664cde3cd355b4aee30f5bfa48c73f6797845cd7719e2938";
const EXPECTED_THRESHOLD: f64 = 0.590596;
const TOKEN_2022_PROGRAM: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const TOKEN_PROGRAM: &str = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const DEFAULT_BUY_LAMPORTS: u64 = 1_000_000;
const DEFAULT_BUY_COMPUTE_UNITS: u32 = 250_000;
const DEFAULT_SELL_COMPUTE_UNITS: u32 = 200_000;
const DEFAULT_PRIORITY_FEE_MICRO_LAMPORTS: u64 = 0;
const PUMP_FEE_BPS: u64 = 125;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct RankerSignal {
    cohort: String,
    mint: String,
    state: String,
    historical_state_alias: Option<String>,
    event_gated_state_ts_ms: u64,
    threshold_decision_ts_ms: u64,
    score: f64,
    score_threshold: f64,
    passes_threshold: bool,
    source_row_id: Option<u64>,
    mc_usd: Option<f64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CandidateCapsule {
    mint: String,
    token_program: Option<String>,
    source_ix_data_base64: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct CanonicalState {
    source_slot: Option<u64>,
    observed_at_ms: Option<u64>,
    market_cap_usd: Option<f64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExecutionFields {
    position_size_lamports: Option<u64>,
    expected_tokens_out_raw: Option<u64>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ExtraFields {
    bonding_curve: Option<String>,
    source_ix_accounts: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct CapsuleRow {
    transaction_signature: Option<String>,
    source_slot: Option<u64>,
    source_transaction_index: Option<u64>,
    candidate: CandidateCapsule,
    canonical_state: Option<CanonicalState>,
    execution: Option<ExecutionFields>,
    extra_decision_fields: Option<ExtraFields>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BuildRequest {
    kind: String,
    signal: RankerSignal,
    capsule: CapsuleRow,
    adapter_receive_unix_ns: u128,
}

#[derive(Debug, Clone)]
struct CachedBlockhash {
    value: String,
    fetched_unix_ns: u128,
}

fn now_ns() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|value| value.as_nanos())
        .unwrap_or(0)
}

fn env_u64(name: &str, default_value: u64) -> u64 {
    env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(default_value)
}

fn validate_signal(signal: &RankerSignal) -> Result<()> {
    if signal.cohort != EXPECTED_COHORT {
        bail!("unexpected Ranker cohort")
    }
    if !signal.passes_threshold || signal.score < EXPECTED_THRESHOLD {
        bail!("signal does not pass frozen threshold")
    }
    if (signal.score_threshold - EXPECTED_THRESHOLD).abs() > f64::EPSILON {
        bail!("threshold mismatch")
    }
    if signal.mint.trim().is_empty() || signal.state.trim().is_empty() {
        bail!("signal identity missing")
    }
    if signal.threshold_decision_ts_ms < signal.event_gated_state_ts_ms {
        bail!("signal timestamps are non-monotonic")
    }
    Ok(())
}

fn token_program(capsule: &CapsuleRow) -> String {
    capsule
        .candidate
        .token_program
        .clone()
        .unwrap_or_else(|| TOKEN_2022_PROGRAM.to_string())
}

fn buy_command(
    request: &BuildRequest,
    blockhash: &CachedBlockhash,
    source_data_override: Option<Option<String>>,
) -> Result<executor::ExecutePumpfunBuyCommand> {
    let signal = &request.signal;
    let capsule = &request.capsule;
    if capsule.candidate.mint != signal.mint {
        bail!("capsule mint does not match Ranker signal")
    }
    let extra = capsule
        .extra_decision_fields
        .as_ref()
        .context("capsule missing extra_decision_fields")?;
    let bonding_curve = extra
        .bonding_curve
        .clone()
        .context("capsule missing bondingCurve")?;
    let token_program_id = token_program(capsule);
    let execution = capsule
        .execution
        .as_ref()
        .context("capsule missing execution fields")?;
    let expected_tokens = execution
        .expected_tokens_out_raw
        .context("capsule missing expected_tokens_out_raw")?;
    let size = env_u64(
        "RANKER_V2_TRANSLATION_POSITION_LAMPORTS",
        execution.position_size_lamports.unwrap_or(DEFAULT_BUY_LAMPORTS),
    );
    let priority = env_u64(
        "RANKER_V2_TRANSLATION_PRIORITY_FEE_MICRO_LAMPORTS",
        DEFAULT_PRIORITY_FEE_MICRO_LAMPORTS,
    );
    let source_accounts = extra.source_ix_accounts.clone();
    let source_data = source_data_override.unwrap_or_else(|| capsule.candidate.source_ix_data_base64.clone());
    let can_source_replay = source_accounts.as_ref().is_some_and(|accounts| !accounts.is_empty())
        && source_data.as_ref().is_some_and(|data| !data.is_empty());
    Ok(executor::ExecutePumpfunBuyCommand {
        id: format!("ranker-v2-{}-buy", signal.mint),
        mode: if can_source_replay {
            "source-replay".to_string()
        } else {
            "direct".to_string()
        },
        route: "ranker-v2-production-translation-pumpfun-buy".to_string(),
        mint: signal.mint.clone(),
        source_signature: capsule.transaction_signature.clone(),
        quote_mint: None,
        sol_in_lamports: size,
        tokens_out_raw: Some(expected_tokens),
        min_tokens_out_raw: Some(1),
        slippage_bps: 0,
        buy_output_policy: Some("fill_first".to_string()),
        exact_sol_in: true,
        source_ix_accounts: source_accounts,
        source_ix_data_base64: source_data,
        bonding_curve: Some(bonding_curve),
        creator: None,
        token_program_id: Some(token_program_id),
        blockhash: blockhash.value.clone(),
        compute_unit_limit: env_u64(
            "RANKER_V2_TRANSLATION_BUY_COMPUTE_UNITS",
            DEFAULT_BUY_COMPUTE_UNITS as u64,
        ) as u32,
        priority_fee_micro_lamports: priority,
        jito_tip_lamports: 0,
        helius_tip_lamports: 0,
        use_jito_direct: false,
        use_jito_fallback: false,
        sender_rpc_fallback_enabled: false,
        execution_multi_send_enabled: false,
        jito_send_mode: "disabled".to_string(),
        jito_transaction_urls: Vec::new(),
        jito_bundle_urls: Vec::new(),
        sender_endpoint_url: String::new(),
        rpc_fallback_url: String::new(),
        jito_auth_uuid: None,
        gate_virtual_sol_reserves: None,
        gate_virtual_token_reserves: None,
        gate_reserve_slot: None,
        gate_reserve_observed_at_ms: None,
        native_entry_drift_gate: false,
        shadow_entry_market_cap_usd: signal.mc_usd,
        shadow_tokens_out_raw: Some(expected_tokens),
        shadow_sol_in_lamports: Some(size),
        max_entry_drift_bps: None,
        landing_buffer_bps: None,
        max_entry_market_cap_usd: None,
    })
}

fn dry_build_buy_with_supported_fallback(
    request: &BuildRequest,
    blockhash: &CachedBlockhash,
) -> Result<(executor::ExecutorResponse, u128, &'static str)> {
    let buy = buy_command(request, blockhash, None)?;
    let buy_build_start_ns = now_ns();
    let buy_started = Instant::now();
    match executor::dry_build_pumpfun_buy_command(buy, buy_started) {
        Ok(result) => Ok((result, buy_build_start_ns, "source_or_direct_primary")),
        Err(error) => {
            let message = error.to_string();
            if !message.contains("source instruction is not a supported pump.fun buy layout") {
                return Err(error);
            }
            let fallback = buy_command(request, blockhash, Some(None))?;
            let fallback_start_ns = now_ns();
            let fallback_started = Instant::now();
            let result = executor::dry_build_pumpfun_buy_command(fallback, fallback_started)
                .with_context(|| format!("source-data fallback failed after unsupported source instruction: {message}"))?;
            Ok((result, fallback_start_ns, "source_accounts_generated_data_after_unsupported_source_data"))
        }
    }
}

fn sell_command(
    request: &BuildRequest,
    buy_tokens: u64,
    blockhash: &CachedBlockhash,
) -> Result<executor::ExecutePumpfunSellCommand> {
    let signal = &request.signal;
    let capsule = &request.capsule;
    let extra = capsule
        .extra_decision_fields
        .as_ref()
        .context("capsule missing extra_decision_fields")?;
    let token_program_id = token_program(capsule);
    let source_accounts = extra
        .source_ix_accounts
        .as_ref()
        .context("sourceIxAccounts missing; sell template unavailable without guessing")?;
    let (sell_v2, watch_accounts) = executor::build_pumpfun_exit_sell_template_from_source_accounts(
        &signal.mint,
        source_accounts,
        Some(&token_program_id),
    )?;
    let accounts = watch_accounts
        .iter()
        .map(|meta| executor::SourcePumpSwapAccountMeta {
            pubkey: meta.pubkey.clone(),
            is_signer: meta.is_signer,
            is_writable: meta.is_writable,
        })
        .collect();
    Ok(executor::ExecutePumpfunSellCommand {
        id: format!("ranker-v2-{}-sell", signal.mint),
        route: "ranker-v2-production-translation-pumpfun-sell".to_string(),
        mint: signal.mint.clone(),
        tokens_in_raw: buy_tokens.max(1),
        expected_sol_out_lamports: request
            .capsule
            .execution
            .as_ref()
            .and_then(|e| e.position_size_lamports)
            .unwrap_or(DEFAULT_BUY_LAMPORTS),
        min_sol_out_lamports: 1,
        sell_v2,
        accounts,
        blockhash: blockhash.value.clone(),
        compute_unit_limit: env_u64(
            "RANKER_V2_TRANSLATION_SELL_COMPUTE_UNITS",
            DEFAULT_SELL_COMPUTE_UNITS as u64,
        ) as u32,
        priority_fee_micro_lamports: env_u64(
            "RANKER_V2_TRANSLATION_PRIORITY_FEE_MICRO_LAMPORTS",
            DEFAULT_PRIORITY_FEE_MICRO_LAMPORTS,
        ),
        jito_tip_lamports: 0,
        helius_tip_lamports: 0,
        use_jito_direct: false,
        use_jito_fallback: false,
        sender_rpc_fallback_enabled: false,
        execution_multi_send_enabled: false,
        jito_send_mode: "disabled".to_string(),
        jito_transaction_urls: Vec::new(),
        jito_bundle_urls: Vec::new(),
        sender_endpoint_url: String::new(),
        rpc_fallback_url: String::new(),
        jito_auth_uuid: None,
    })
}

fn process_request(request: BuildRequest, blockhash: &CachedBlockhash) -> Result<Value> {
    let rust_receive_ns = now_ns();
    if request.kind != REQUEST_KIND {
        bail!("unsupported request kind")
    }
    validate_signal(&request.signal)?;
    let blockhash_age_ms = rust_receive_ns.saturating_sub(blockhash.fetched_unix_ns) / 1_000_000;
    if blockhash_age_ms > 15_000 {
        bail!("cached blockhash stale")
    }
    let intent_ns = now_ns();
    let (buy_result, buy_build_start_ns, buy_template_mode) =
        dry_build_buy_with_supported_fallback(&request, blockhash)?;
    let buy_ready_ns = now_ns();
    if !buy_result.ok || buy_result.sender_path.as_deref() != Some("dry-build-no-submit") {
        bail!("buy dry-build safety invariant failed")
    }
    let buy_tokens = buy_result.tokens_out_raw.unwrap_or(1).max(1);
    let sell = sell_command(&request, buy_tokens, blockhash)?;
    let sell_decision_ns = now_ns();
    let sell_build_start_ns = now_ns();
    let sell_started = Instant::now();
    let sell_result = executor::dry_build_pumpfun_sell_command(sell, sell_started)?;
    let sell_ready_ns = now_ns();
    if !sell_result.ok || sell_result.sender_path.as_deref() != Some("dry-build-no-submit") {
        bail!("sell dry-build safety invariant failed")
    }
    let decision_ns = (request.signal.threshold_decision_ts_ms as u128) * 1_000_000;
    Ok(json!({
        "schema": "ranker-v2-production-translation-no-submit-result-v1",
        "kind": "rankerV2ProductionNoSubmitBuildResult",
        "ok": true,
        "cohort": EXPECTED_COHORT,
        "canonicalScorerSha256": EXPECTED_SPEC_SHA256,
        "canonicalObjectSha256": EXPECTED_OBJECT_SHA256,
        "threshold": EXPECTED_THRESHOLD,
        "signalId": format!("{}:{}:{}", request.signal.mint, request.signal.event_gated_state_ts_ms, request.signal.score),
        "mint": request.signal.mint,
        "state": request.signal.state,
        "historicalStateAlias": request.signal.historical_state_alias,
        "sourceRowId": request.signal.source_row_id,
        "sourceSlot": request.capsule.source_slot.or_else(|| request.capsule.canonical_state.as_ref().and_then(|s| s.source_slot)),
        "sourceTransactionIndex": request.capsule.source_transaction_index,
        "sourceSignature": request.capsule.transaction_signature,
        "stateTsMs": request.signal.event_gated_state_ts_ms,
        "shadowDecisionTsMs": request.signal.threshold_decision_ts_ms,
        "adapterReceiveUnixNs": request.adapter_receive_unix_ns,
        "rustReceiveUnixNs": rust_receive_ns,
        "rustIntentUnixNs": intent_ns,
        "buyBuildStartUnixNs": buy_build_start_ns,
        "buyTxReadyUnixNs": buy_ready_ns,
        "blockedFirstSendUnixNs": buy_ready_ns,
        "sellDecisionKnownUnixNs": sell_decision_ns,
        "sellBuildStartUnixNs": sell_build_start_ns,
        "sellTxReadyUnixNs": sell_ready_ns,
        "softwareDecisionToAdapterUs": request.adapter_receive_unix_ns.saturating_sub(decision_ns) / 1000,
        "adapterToRustReceiveUs": rust_receive_ns.saturating_sub(request.adapter_receive_unix_ns) / 1000,
        "productionReceiveToIntentUs": intent_ns.saturating_sub(request.adapter_receive_unix_ns) / 1000,
        "intentToTxReadyUs": buy_ready_ns.saturating_sub(intent_ns) / 1000,
        "shadowDecisionToTxReadyUs": buy_ready_ns.saturating_sub(decision_ns) / 1000,
        "shadowDecisionToFirstSendBoundaryUs": buy_ready_ns.saturating_sub(decision_ns) / 1000,
        "buyBuildDurationUs": buy_ready_ns.saturating_sub(buy_build_start_ns) / 1000,
        "sellDecisionToIntentUs": 0,
        "sellIntentToTxReadyUs": sell_ready_ns.saturating_sub(sell_decision_ns) / 1000,
        "sellDecisionToFirstSendBoundaryUs": sell_ready_ns.saturating_sub(sell_decision_ns) / 1000,
        "cachedBlockhashAgeMs": blockhash_age_ms,
        "tokenProgram": token_program(&request.capsule),
        "route": "Pump.fun direct/source-account-aware dry build",
        "buyTemplateMode": buy_template_mode,
        "instructionVariant": if token_program(&request.capsule) == TOKEN_2022_PROGRAM { "pumpfun-v2-fill-first-exact-sol" } else { "pumpfun-legacy-fill-first-exact-sol" },
        "buy": buy_result,
        "sellProbe": sell_result,
        "inventorySource": "NO_SUBMIT_SYNTHETIC_BUY_RESULT_TOKENS",
        "tpPct": 20.0,
        "slPct": -20.0,
        "maxHoldMs": 120000,
        "firstSellableTelemetry": "NOT_LIVE_MEASURED_IN_THIS_NO_SUBMIT_TASK",
        "submissionCount": 0,
        "broadcastSuppressed": true,
    }))
}

async fn refresh_blockhash(cache: Arc<Mutex<Option<CachedBlockhash>>>, rpc_url: String) {
    loop {
        match executor::fetch_latest_blockhash(&rpc_url).await {
            Ok(value) => {
                if let Ok(mut guard) = cache.lock() {
                    *guard = Some(CachedBlockhash {
                        value,
                        fetched_unix_ns: now_ns(),
                    });
                }
            }
            Err(error) => eprintln!("ranker-v2 no-submit blockhash refresh failed: {error:#}"),
        }
        sleep(Duration::from_millis(1_000)).await;
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let keypair = Keypair::new();
    env::set_var("PRIVATE_KEY_BASE58", bs58::encode(keypair.to_bytes()).into_string());
    env::set_var("RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS", PUMP_FEE_BPS.to_string());
    env::set_var("RUST_SELECTED_CANDIDATE_SUBMIT_ENABLED", "false");

    let rpc_url = env::var("READ_ONLY_BLOCKHASH_RPC_URL")
        .unwrap_or_else(|_| "https://api.mainnet-beta.solana.com".to_string());
    let cache: Arc<Mutex<Option<CachedBlockhash>>> = Arc::new(Mutex::new(None));
    tokio::spawn(refresh_blockhash(cache.clone(), rpc_url));
    let wait_started = Instant::now();
    while cache.lock().ok().and_then(|guard| guard.clone()).is_none() {
        if wait_started.elapsed() > Duration::from_secs(15) {
            return Err(anyhow!("no read-only blockhash available after 15s"));
        }
        sleep(Duration::from_millis(25)).await;
    }

    println!(
        "{}",
        json!({
            "schema": "ranker-v2-production-translation-builder-ready-v1",
            "kind": "rankerV2ProductionNoSubmitBuilderReady",
            "atUnixNs": now_ns(),
            "shadowSigner": keypair.pubkey().to_string(),
            "broadcastCommandsExposed": false,
            "submissionCount": 0,
        })
    );

    let stdin = BufReader::new(io::stdin());
    let mut lines = stdin.lines();
    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        let id = serde_json::from_str::<Value>(&line)
            .ok()
            .and_then(|value| value.pointer("/signal/mint").and_then(Value::as_str).map(str::to_string));
        let result = (|| -> Result<Value> {
            let request: BuildRequest = serde_json::from_str(&line).context("invalid build request")?;
            let blockhash = cache
                .lock()
                .map_err(|_| anyhow!("blockhash cache lock poisoned"))?
                .clone()
                .context("blockhash cache empty")?;
            process_request(request, &blockhash)
        })();
        match result {
            Ok(value) => println!("{value}"),
            Err(error) => println!(
                "{}",
                json!({
                    "schema": "ranker-v2-production-translation-no-submit-result-v1",
                    "kind": "rankerV2ProductionNoSubmitBuildResult",
                    "ok": false,
                    "signalId": id,
                    "error": error.to_string(),
                    "submissionCount": 0,
                    "broadcastSuppressed": true,
                })
            ),
        }
    }
    Ok(())
}
