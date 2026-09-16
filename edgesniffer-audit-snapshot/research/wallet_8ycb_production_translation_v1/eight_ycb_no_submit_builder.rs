//! 8YCB production-translation build-only probe.
//!
//! This binary deliberately exposes only the two production executor dry-build
//! functions.  It creates and signs real Pump.fun buy/sell transactions using
//! the deployed executor implementation, but it has no command or branch that
//! calls an execute/broadcast function.

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

const SIGNAL_SCHEMA: &str = "8ycb-production-signal-v1";
const REQUEST_KIND: &str = "build8ycbProductionNoSubmit";
const TARGET_WALLET: &str = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ";
const FROZEN_SPEC_SHA256: &str =
    "6f42c24221a50341c8c5626ae3a987b564065ba0c9de9a1e3d948fa5ea432817";
const TOKEN_2022_PROGRAM: &str = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const INITIAL_VIRTUAL_SOL: u64 = 30_000_000_000;
const INITIAL_VIRTUAL_TOKENS: u64 = 1_073_000_000_000_000;
const BUY_LAMPORTS: u64 = 100_000_000;
const BUY_COMPUTE_UNITS: u32 = 250_000;
const SELL_COMPUTE_UNITS: u32 = 200_000;
const PRIORITY_FEE_MICRO_LAMPORTS: u64 = 18_000_000;
const BASE_FEE_LAMPORTS: u64 = 5_000;
const PUMP_FEE_BPS: u64 = 125;

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct FrozenSignal {
    schema: String,
    mint: String,
    create_signature: String,
    slot: u64,
    transaction_index: Option<u64>,
    target_wallet: String,
    atomic_target_buy_lamports: u64,
    atomic_target_tokens_out_raw: Option<u64>,
    create_receive_unix_ns: u128,
    final_trigger_input_receive_unix_ns: u128,
    signal_ready_unix_ns: u128,
    unique_signal_id: String,
    frozen_spec_sha256: String,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct BuildRequest {
    kind: String,
    signal: FrozenSignal,
    bonding_curve: String,
    token_program_id: String,
    adapter_receipt_unix_ns: u128,
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

fn validate_signal(signal: &FrozenSignal) -> Result<()> {
    if signal.schema != SIGNAL_SCHEMA {
        bail!("unexpected signal schema")
    }
    if signal.frozen_spec_sha256 != FROZEN_SPEC_SHA256 {
        bail!("frozen spec hash mismatch")
    }
    if signal.target_wallet != TARGET_WALLET {
        bail!("target wallet mismatch")
    }
    if signal.mint.trim().is_empty()
        || signal.create_signature.trim().is_empty()
        || signal.unique_signal_id.trim().is_empty()
    {
        bail!("signal identity field is empty")
    }
    if signal.slot == 0 || signal.atomic_target_buy_lamports == 0 {
        bail!("signal numeric field is zero")
    }
    if signal.atomic_target_tokens_out_raw == Some(0) {
        bail!("optional atomic target token amount is zero")
    }
    if signal.final_trigger_input_receive_unix_ns < signal.create_receive_unix_ns
        || signal.signal_ready_unix_ns < signal.final_trigger_input_receive_unix_ns
    {
        bail!("signal timestamps are not monotonic")
    }
    Ok(())
}

fn ceil_div(value: u128, divisor: u128) -> u128 {
    value.saturating_add(divisor.saturating_sub(1)) / divisor
}

fn curve_sol_after_total_fee(total_lamports: u64) -> u64 {
    let gross = total_lamports as u128;
    let net = gross.saturating_mul(10_000) / (10_000 + PUMP_FEE_BPS as u128);
    let fee = ceil_div(net.saturating_mul(PUMP_FEE_BPS as u128), 10_000);
    let adjustment = net.saturating_add(fee).saturating_sub(gross);
    net.saturating_sub(adjustment).saturating_sub(1) as u64
}

fn post_atomic_curve(signal: &FrozenSignal) -> Result<exit_watcher::CurveState> {
    let invariant = (INITIAL_VIRTUAL_SOL as u128) * (INITIAL_VIRTUAL_TOKENS as u128);
    let (virtual_tokens, virtual_sol) = if let Some(tokens_out) = signal.atomic_target_tokens_out_raw {
        let virtual_tokens = INITIAL_VIRTUAL_TOKENS
            .checked_sub(tokens_out)
            .context("atomic target token amount exceeds initial virtual reserve")?;
        let virtual_sol = u64::try_from(invariant / virtual_tokens as u128)
            .context("post-atomic virtual SOL overflow")?;
        (virtual_tokens, virtual_sol)
    } else {
        // Tokens-out is useful construction telemetry but is not part of the
        // frozen qualifying decision.  Never turn its absence into an FN.
        let virtual_sol = INITIAL_VIRTUAL_SOL
            .checked_add(curve_sol_after_total_fee(signal.atomic_target_buy_lamports))
            .context("post-atomic virtual SOL overflow")?;
        let virtual_tokens = u64::try_from(invariant / virtual_sol as u128)
            .context("post-atomic virtual token overflow")?;
        (virtual_tokens, virtual_sol)
    };
    Ok(exit_watcher::CurveState {
        virtual_token_reserves: virtual_tokens,
        virtual_sol_reserves: virtual_sol,
        complete: false,
    })
}

fn quote_buy(state: exit_watcher::CurveState, total_lamports: u64) -> Result<(u64, exit_watcher::CurveState)> {
    let curve_sol = curve_sol_after_total_fee(total_lamports);
    let invariant = (state.virtual_sol_reserves as u128)
        .checked_mul(state.virtual_token_reserves as u128)
        .context("buy quote invariant overflow")?;
    let post_sol = state.virtual_sol_reserves.saturating_add(curve_sol);
    let post_tokens = u64::try_from(invariant / post_sol as u128)
        .context("buy quote token reserve overflow")?;
    let tokens_out = state
        .virtual_token_reserves
        .checked_sub(post_tokens)
        .context("buy quote produced no tokens")?;
    Ok((
        tokens_out.max(1),
        exit_watcher::CurveState {
            virtual_token_reserves: post_tokens,
            virtual_sol_reserves: post_sol,
            complete: false,
        },
    ))
}

fn execution_fee_lamports(compute_units: u32) -> u64 {
    (compute_units as u64)
        .saturating_mul(PRIORITY_FEE_MICRO_LAMPORTS)
        / 1_000_000
        + BASE_FEE_LAMPORTS
}

fn sell_template(
    signal: &FrozenSignal,
    bonding_curve: &str,
    token_program_id: &str,
) -> Result<exit_watcher::PumpfunSellTemplate> {
    let (sell_v2, accounts) = executor::build_pumpfun_exit_sell_template(
        &signal.mint,
        bonding_curve,
        &signal.target_wallet,
        Some(token_program_id),
    )?;
    if token_program_id == TOKEN_2022_PROGRAM && !sell_v2 {
        bail!("Token-2022 signal did not produce a Pump.fun V2 sell template")
    }
    Ok(exit_watcher::PumpfunSellTemplate {
        sell_v2,
        accounts,
        compute_unit_limit: SELL_COMPUTE_UNITS,
        priority_fee_micro_lamports: PRIORITY_FEE_MICRO_LAMPORTS,
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

fn thresholds() -> exit_watcher::ExitThresholds {
    exit_watcher::ExitThresholds {
        stop_loss_pct: 20.0,
        stop_loss_grace_ms: 0,
        take_profit_pct: 20.0,
        take_profit_sell_pct: 100.0,
        early_source_window_ms: 0,
        early_source_max_pnl_pct: 0.0,
        source_partial_sell_pct: 100.0,
        runner_drawdown_pct: 20.0,
        max_hold_ms: 120_000,
        sell_slippage_bps: 500,
        sell_trade_fee_bps: PUMP_FEE_BPS,
        normal_take_profit_capture_slippage_bps: 500,
        estimated_execution_fee_lamports: execution_fee_lamports(SELL_COMPUTE_UNITS),
        decision_execution_fee_lamports: Some(execution_fee_lamports(SELL_COMPUTE_UNITS)),
        tp_6003_salvage_min_realized_pnl_pct: None,
        tp_6003_salvage_authority_max_age_ms: 250,
        min_acceptable_take_profit_pnl_pct: None,
    }
}

fn state_for_pnl(
    entry_state: exit_watcher::CurveState,
    tokens: u64,
    desired: &str,
) -> Result<exit_watcher::CurveState> {
    let invariant = (entry_state.virtual_sol_reserves as u128)
        .checked_mul(entry_state.virtual_token_reserves as u128)
        .context("sell probe invariant overflow")?;
    let sell_fee = execution_fee_lamports(SELL_COMPUTE_UNITS);
    for step in 1..=2_500_u64 {
        let bps = match desired {
            "take_profit" => 10_000_u64.saturating_add(step * 4),
            "stop_loss" => 10_000_u64.saturating_sub(step * 2).max(1),
            _ => bail!("unknown desired PnL state"),
        };
        let virtual_sol = ((entry_state.virtual_sol_reserves as u128) * bps as u128 / 10_000)
            .max(1) as u64;
        let virtual_tokens = (invariant / virtual_sol as u128).max(1) as u64;
        let state = exit_watcher::CurveState {
            virtual_token_reserves: virtual_tokens,
            virtual_sol_reserves: virtual_sol,
            complete: false,
        };
        if let Some(out) = exit_watcher::quote_sell_lamports_with_fee_bps(state, tokens, PUMP_FEE_BPS) {
            let pnl = exit_watcher::fee_adjusted_pnl_pct(out, BUY_LAMPORTS, sell_fee);
            if (desired == "take_profit" && pnl >= 20.0)
                || (desired == "stop_loss" && pnl <= -20.0)
            {
                return Ok(state);
            }
        }
    }
    bail!("could not construct {desired} exit state")
}

fn exercise_sell_scenario(
    signal: &FrozenSignal,
    bonding_curve: &str,
    blockhash: &str,
    tokens: u64,
    entry_state: exit_watcher::CurveState,
    template: exit_watcher::PumpfunSellTemplate,
    scenario: &str,
) -> Result<Value> {
    let registry = exit_watcher::ExitWatcherRegistry::default();
    let entry_ms = (now_ns() / 1_000_000) as u64;
    let register = registry.register(exit_watcher::StartPumpfunExitWatchCommand {
        id: format!("{}-{scenario}-register", signal.unique_signal_id),
        mint: signal.mint.clone(),
        source_wallet: signal.target_wallet.clone(),
        bonding_curve: bonding_curve.to_string(),
        entry_ts_ms: entry_ms,
        tokens_held_raw: tokens,
        safe_sell_tokens_raw: None,
        cost_basis_lamports: BUY_LAMPORTS,
        buy_confirmed: true,
        exact_balance_proof: true,
        pre_proof_tp_only: false,
        mark_telemetry_ms: 1,
        thresholds: thresholds(),
        sell: template,
        shadow: true,
    });
    if !register.ok {
        bail!("exit registration failed: {:?}", register.error)
    }

    let (state, decision_ms, expected_reason) = match scenario {
        "take_profit" => (
            state_for_pnl(entry_state, tokens, "take_profit")?,
            entry_ms + 1,
            "take-profit",
        ),
        "stop_loss" => (
            state_for_pnl(entry_state, tokens, "stop_loss")?,
            entry_ms + 1,
            "hard-stop-loss",
        ),
        "timeout" => (entry_state, entry_ms + 120_000, "max-hold"),
        _ => bail!("unknown sell scenario"),
    };

    let decision_started = Instant::now();
    let decision_started_ns = now_ns();
    let (_mark, action, _fallback) = registry.on_curve_state(
        bonding_curve,
        state,
        signal.slot,
        decision_ms,
        "8ycb-no-submit-fixture",
    );
    let action = action.context("exit watcher produced no action")?;
    if action.reason.as_str() != expected_reason {
        bail!(
            "exit watcher reason mismatch: expected {expected_reason}, got {}",
            action.reason.as_str()
        )
    }
    let sell_intent_ns = now_ns();
    let duplicate_before_ack = registry
        .on_curve_state(
            bonding_curve,
            state,
            signal.slot,
            decision_ms + 1,
            "8ycb-no-submit-fixture",
        )
        .1
        .is_some();

    let command = executor::ExecutePumpfunSellCommand {
        id: format!("{}-{scenario}-sell", signal.unique_signal_id),
        route: "pumpfun-rust-8ycb-no-submit-sell".to_string(),
        mint: signal.mint.clone(),
        tokens_in_raw: action.tokens_in_raw,
        expected_sol_out_lamports: action.expected_sol_out_lamports,
        min_sol_out_lamports: action.min_sol_out_lamports,
        sell_v2: action.sell.sell_v2,
        accounts: action
            .sell
            .accounts
            .iter()
            .map(|meta| executor::SourcePumpSwapAccountMeta {
                pubkey: meta.pubkey.clone(),
                is_signer: meta.is_signer,
                is_writable: meta.is_writable,
            })
            .collect(),
        blockhash: blockhash.to_string(),
        compute_unit_limit: action.sell.compute_unit_limit,
        priority_fee_micro_lamports: action.sell.priority_fee_micro_lamports,
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
    };
    let sell_build_started_ns = now_ns();
    let response = executor::dry_build_pumpfun_sell_command(command, decision_started)?;
    let sell_ready_ns = now_ns();

    let (ack_status, duplicate_from_ack) = registry.ack(exit_watcher::AckPumpfunExitSellCommand {
        id: format!("{}-{scenario}-ack", signal.unique_signal_id),
        mint: signal.mint.clone(),
        sequence: action.sequence,
        ok: true,
        actual_sol_out_lamports: Some(action.expected_sol_out_lamports),
        error: None,
        terminal_sell_signature: Some("NO_SUBMIT_FIXTURE_ACK".to_string()),
        terminal_slot: Some(signal.slot),
        terminal_at_ms: Some(decision_ms + 2),
    });
    let duplicate_after_ack = duplicate_from_ack.is_some()
        || registry
            .on_curve_state(
                bonding_curve,
                state,
                signal.slot,
                decision_ms + 3,
                "8ycb-no-submit-fixture",
            )
            .1
            .is_some();

    Ok(json!({
        "scenario": scenario,
        "decisionReason": action.reason.as_str(),
        "exitDecisionUnixNs": decision_started_ns,
        "sellIntentCreatedUnixNs": sell_intent_ns,
        "sellBuildStartUnixNs": sell_build_started_ns,
        "sellSigningReadyUnixNs": sell_ready_ns,
        "sellTransactionReadyUnixNs": sell_ready_ns,
        "blockedSubmissionUnixNs": sell_ready_ns,
        "decisionToIntentUs": sell_intent_ns.saturating_sub(decision_started_ns) / 1_000,
        "intentToTxReadyUs": sell_ready_ns.saturating_sub(sell_intent_ns) / 1_000,
        "decisionToTxReadyUs": sell_ready_ns.saturating_sub(decision_started_ns) / 1_000,
        "decisionProbeElapsedUs": decision_started.elapsed().as_micros(),
        "tokensInRaw": action.tokens_in_raw,
        "expectedSolOutLamports": action.expected_sol_out_lamports,
        "minSolOutLamports": action.min_sol_out_lamports,
        "feeAdjustedPnlPct": action.fee_adjusted_pnl_pct,
        "dryBuild": response,
        "duplicateSellBeforeAck": duplicate_before_ack,
        "duplicateSellAfterTerminalAck": duplicate_after_ack,
        "terminalAckOk": ack_status.ok,
        "inventorySource": "SYNTHETIC_MODELED_NO_SUBMIT_FIXTURE",
        "exactBalanceProof": "EXERCISED_AS_DETERMINISTIC_FIXTURE_NOT_LIVE_PROOF",
        "submissionCount": 0,
    }))
}

fn process_request(request: BuildRequest, blockhash: &CachedBlockhash) -> Result<Value> {
    let received_ns = now_ns();
    if request.kind != REQUEST_KIND {
        bail!("unsupported request kind")
    }
    validate_signal(&request.signal)?;
    let bonding_curve = if request.bonding_curve.trim().is_empty() {
        executor::pumpfun_bonding_curve_v2_pda_for_mint(&request.signal.mint)
            .context("could not derive canonical Pump.fun V2 bonding curve")?
    } else {
        request.bonding_curve.clone()
    };
    let token_program_id = if request.token_program_id.trim().is_empty() {
        TOKEN_2022_PROGRAM.to_string()
    } else {
        request.token_program_id.clone()
    };
    if token_program_id != TOKEN_2022_PROGRAM {
        bail!("frozen CreateV2 trigger requires the Token-2022 program")
    }
    let blockhash_age_ms = received_ns.saturating_sub(blockhash.fetched_unix_ns) / 1_000_000;
    if blockhash_age_ms > 15_000 {
        bail!("cached blockhash is stale")
    }

    let (modeled_tokens, entry_state) = quote_buy(post_atomic_curve(&request.signal)?, BUY_LAMPORTS)?;
    let rust_intent_created_ns = now_ns();
    let command = executor::ExecutePumpfunBuyCommand {
        id: format!("{}-buy", request.signal.unique_signal_id),
        mode: "direct".to_string(),
        route: "pumpfun-rust-8ycb-no-submit-buy".to_string(),
        mint: request.signal.mint.clone(),
        source_signature: Some(request.signal.create_signature.clone()),
        quote_mint: None,
        sol_in_lamports: BUY_LAMPORTS,
        tokens_out_raw: Some(modeled_tokens),
        min_tokens_out_raw: Some(1),
        slippage_bps: 0,
        buy_output_policy: Some("fill_first".to_string()),
        exact_sol_in: true,
        source_ix_accounts: None,
        source_ix_data_base64: None,
        bonding_curve: Some(bonding_curve.clone()),
        creator: Some(request.signal.target_wallet.clone()),
        token_program_id: Some(token_program_id.clone()),
        blockhash: blockhash.value.clone(),
        compute_unit_limit: BUY_COMPUTE_UNITS,
        priority_fee_micro_lamports: PRIORITY_FEE_MICRO_LAMPORTS,
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
        shadow_entry_market_cap_usd: None,
        shadow_tokens_out_raw: None,
        shadow_sol_in_lamports: None,
        max_entry_drift_bps: None,
        landing_buffer_bps: None,
        max_entry_market_cap_usd: None,
    };

    let buy_started = Instant::now();
    let buy_build_start_ns = now_ns();
    let buy_response = executor::dry_build_pumpfun_buy_command(command, buy_started)?;
    let buy_ready_ns = now_ns();
    if !buy_response.ok || buy_response.sender_path.as_deref() != Some("dry-build-no-submit") {
        bail!("production executor did not return a successful no-submit dry build")
    }

    let template = sell_template(
        &request.signal,
        &bonding_curve,
        &token_program_id,
    )?;
    let mut sell_probes = Vec::new();
    for scenario in ["take_profit", "stop_loss", "timeout"] {
        sell_probes.push(exercise_sell_scenario(
            &request.signal,
            &bonding_curve,
            &blockhash.value,
            modeled_tokens,
            entry_state,
            template.clone(),
            scenario,
        )?);
    }

    Ok(json!({
        "schema": "8ycb-production-no-submit-build-result-v1",
        "kind": "8ycbProductionNoSubmitBuildResult",
        "ok": true,
        "signalId": request.signal.unique_signal_id,
        "mint": request.signal.mint,
        "createSignature": request.signal.create_signature,
        "slot": request.signal.slot,
        "transactionIndex": request.signal.transaction_index,
        "targetWallet": request.signal.target_wallet,
        "adapterReceiptUnixNs": request.adapter_receipt_unix_ns,
        "rustRequestReceiptUnixNs": received_ns,
        "rustIntentCreatedUnixNs": rust_intent_created_ns,
        "buyBuildStartUnixNs": buy_build_start_ns,
        "buySigningReadyUnixNs": buy_ready_ns,
        "buyTransactionReadyUnixNs": buy_ready_ns,
        "blockedSubmissionUnixNs": buy_ready_ns,
        "v3SignalToProductionReceiptUs": request.adapter_receipt_unix_ns.saturating_sub(request.signal.signal_ready_unix_ns) / 1_000,
        "productionReceiptToIntentUs": rust_intent_created_ns.saturating_sub(request.adapter_receipt_unix_ns) / 1_000,
        "intentToBuyTxReadyUs": buy_ready_ns.saturating_sub(rust_intent_created_ns) / 1_000,
        "v3SignalToBuyTxReadyUs": buy_ready_ns.saturating_sub(request.signal.signal_ready_unix_ns) / 1_000,
        "buyBuildDurationUs": buy_ready_ns.saturating_sub(buy_build_start_ns) / 1_000,
        "cachedBlockhashAgeMs": blockhash_age_ms,
        "modeledTokensOutRaw": modeled_tokens,
        "buy": buy_response,
        "sellProbes": sell_probes,
        "construction": {
            "route": "Pump.fun direct derived V2 accounts",
            "bondingCurve": bonding_curve,
            "tokenProgramId": token_program_id,
            "sizeLamports": BUY_LAMPORTS,
            "buyComputeUnitLimit": BUY_COMPUTE_UNITS,
            "sellComputeUnitLimit": SELL_COMPUTE_UNITS,
            "priorityFeeMicroLamports": PRIORITY_FEE_MICRO_LAMPORTS,
            "jitoTipLamports": 0,
            "heliusTipLamports": 0,
            "signer": "EPHEMERAL_OFFLINE_UNFUNDED_PROCESS_KEYPAIR",
        },
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
            Err(error) => eprintln!("8ycb build-only blockhash refresh failed: {error:#}"),
        }
        sleep(Duration::from_millis(1_000)).await;
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let _ = rustls::crypto::ring::default_provider().install_default();
    let keypair = Keypair::new();
    let shadow_signer = keypair.pubkey().to_string();
    env::set_var("PRIVATE_KEY_BASE58", bs58::encode(keypair.to_bytes()).into_string());
    env::set_var("RUST_SELECTED_CANDIDATE_PUMPFUN_TOTAL_FEE_BPS", PUMP_FEE_BPS.to_string());
    env::set_var("RUST_SELECTED_CANDIDATE_FEE_QUOTE_HAIRCUT_BPS", "0");
    env::set_var("RUST_SELECTED_CANDIDATE_MAX_MIN_OUT_BELOW_PROTECTED_BPS", "0");

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
            "schema": "8ycb-production-no-submit-builder-ready-v1",
            "kind": "8ycbProductionNoSubmitBuilderReady",
            "atUnixNs": now_ns(),
            "shadowSigner": shadow_signer,
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
            .and_then(|value| value.pointer("/signal/uniqueSignalId").and_then(Value::as_str).map(str::to_string));
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
                    "schema": "8ycb-production-no-submit-build-result-v1",
                    "kind": "8ycbProductionNoSubmitBuildResult",
                    "ok": false,
                    "signalId": id,
                    "error": error.to_string(),
                    "submissionCount": 0,
                })
            ),
        }
    }
    Ok(())
}
