use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const DEFAULT_PUMP_FUN_SELL_TRADE_FEE_BPS: u64 = 125;
const FAILED_EXIT_RETRY_COOLDOWN_MS: u64 = 5_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ExitPhase {
    OpenGrace,
    Open,
    Runner,
    Tp6003SalvageArmed,
    Exiting,
    SellSentStatusUnknown,
    ExitFailedRecoverable,
    Closed,
    Graduated,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ExitReason {
    SourceFlat,
    EarlySourceSell,
    LosingSourceSell,
    SourcePartial,
    TakeProfit,
    RunnerSourceSell,
    RunnerDrawdown,
    HardStopLoss,
    MaxHold,
}

impl ExitReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::SourceFlat => "source-flat",
            Self::EarlySourceSell => "early-source-sell",
            Self::LosingSourceSell => "losing-source-sell",
            Self::SourcePartial => "source-partial",
            Self::TakeProfit => "take-profit",
            Self::RunnerSourceSell => "runner-source-sell",
            Self::RunnerDrawdown => "runner-drawdown",
            Self::HardStopLoss => "hard-stop-loss",
            Self::MaxHold => "max-hold",
        }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExitThresholds {
    pub stop_loss_pct: f64,
    pub stop_loss_grace_ms: u64,
    pub take_profit_pct: f64,
    pub take_profit_sell_pct: f64,
    pub early_source_window_ms: u64,
    pub early_source_max_pnl_pct: f64,
    pub source_partial_sell_pct: f64,
    pub runner_drawdown_pct: f64,
    #[serde(default)]
    pub max_hold_ms: u64,
    #[serde(default = "default_sell_slippage_bps")]
    pub sell_slippage_bps: u64,
    #[serde(default = "default_sell_trade_fee_bps")]
    pub sell_trade_fee_bps: u64,
    #[serde(default = "default_normal_take_profit_capture_slippage_bps")]
    pub normal_take_profit_capture_slippage_bps: u64,
    #[serde(default)]
    pub estimated_execution_fee_lamports: u64,
    #[serde(default)]
    pub decision_execution_fee_lamports: Option<u64>,
    #[serde(default)]
    pub tp_6003_salvage_min_realized_pnl_pct: Option<f64>,
    #[serde(default = "default_tp_6003_salvage_authority_max_age_ms")]
    pub tp_6003_salvage_authority_max_age_ms: u64,
    /// Deprecated compatibility field. It must not drive the first normal TP
    /// transaction; emergency 6003 salvage receives an explicit floor.
    #[serde(default)]
    pub min_acceptable_take_profit_pnl_pct: Option<f64>,
}

fn default_sell_slippage_bps() -> u64 {
    9_000
}

fn default_sell_trade_fee_bps() -> u64 {
    DEFAULT_PUMP_FUN_SELL_TRADE_FEE_BPS
}

fn default_normal_take_profit_capture_slippage_bps() -> u64 {
    default_sell_slippage_bps()
}

fn default_tp_6003_salvage_authority_max_age_ms() -> u64 {
    250
}

impl ExitThresholds {
    fn valid(&self) -> bool {
        self.stop_loss_pct > 0.0
            && self.take_profit_pct > 0.0
            && (0.0..=100.0).contains(&self.take_profit_sell_pct)
            && self.take_profit_sell_pct > 0.0
            && (0.0..=100.0).contains(&self.source_partial_sell_pct)
            && self.source_partial_sell_pct > 0.0
            && (0.0..100.0).contains(&self.runner_drawdown_pct)
            && self.runner_drawdown_pct > 0.0
            && self.sell_slippage_bps <= 10_000
            && self.sell_trade_fee_bps <= 10_000
            && self.normal_take_profit_capture_slippage_bps <= 10_000
            && self
                .tp_6003_salvage_min_realized_pnl_pct
                .map(|value| value.is_finite() && value > -100.0)
                .unwrap_or(true)
            && self.tp_6003_salvage_authority_max_age_ms > 0
            && self
                .min_acceptable_take_profit_pnl_pct
                .map(|value| value.is_finite() && value > -100.0)
                .unwrap_or(true)
    }
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WatchAccountMeta {
    pub pubkey: String,
    pub is_signer: bool,
    pub is_writable: bool,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PumpfunSellTemplate {
    pub sell_v2: bool,
    pub accounts: Vec<WatchAccountMeta>,
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

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct StartPumpfunExitWatchCommand {
    pub id: String,
    pub mint: String,
    pub source_wallet: String,
    pub bonding_curve: String,
    pub entry_ts_ms: u64,
    pub tokens_held_raw: u64,
    #[serde(default)]
    pub safe_sell_tokens_raw: Option<u64>,
    pub cost_basis_lamports: u64,
    #[serde(default)]
    pub buy_confirmed: bool,
    #[serde(default)]
    pub exact_balance_proof: bool,
    #[serde(default)]
    pub pre_proof_tp_only: bool,
    #[serde(default = "default_mark_telemetry_ms")]
    pub mark_telemetry_ms: u64,
    pub thresholds: ExitThresholds,
    pub sell: PumpfunSellTemplate,
    #[serde(default)]
    pub shadow: bool,
}

fn default_mark_telemetry_ms() -> u64 {
    1_000
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdatePumpfunExitWatchCommand {
    pub id: String,
    pub mint: String,
    pub tokens_held_raw: u64,
    pub cost_basis_lamports: u64,
    #[serde(default)]
    pub exact_proof: bool,
    #[serde(default)]
    pub proof_source: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CancelPumpfunExitWatchCommand {
    pub id: String,
    pub mint: String,
    pub reason: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceFlatPumpfunExitWatchCommand {
    pub id: String,
    pub mint: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SourceSellPumpfunExitWatchCommand {
    pub id: String,
    pub mint: String,
    pub source_wallet: String,
    pub source_signature: String,
    #[serde(default)]
    pub sold_tokens_raw: Option<u64>,
    /// When true, this sell also proves the tracked source has no remaining
    /// local position. Applying both facts under one registry lock prevents
    /// source-sell/source-flat command reordering from losing the remainder.
    #[serde(default)]
    pub source_flat: bool,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ActivatePumpfunExitWatchCommand {
    pub id: String,
    pub mint: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AckPumpfunExitSellCommand {
    pub id: String,
    pub mint: String,
    pub sequence: u64,
    pub ok: bool,
    pub actual_sol_out_lamports: Option<u64>,
    pub error: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
enum WatcherCommand {
    StartPumpfunExitWatch(StartPumpfunExitWatchCommand),
    UpdatePumpfunExitWatch(UpdatePumpfunExitWatchCommand),
    CancelPumpfunExitWatch(CancelPumpfunExitWatchCommand),
    SourceFlatPumpfunExitWatch(SourceFlatPumpfunExitWatchCommand),
    SourceSellPumpfunExitWatch(SourceSellPumpfunExitWatchCommand),
    ActivatePumpfunExitWatch(ActivatePumpfunExitWatchCommand),
    AckPumpfunExitSell(AckPumpfunExitSellCommand),
}

pub fn handle_watcher_command_line(
    line: &str,
    registry: &ExitWatcherRegistry,
    now_ms: u64,
) -> Option<(ExitStatusLine, Option<ExitAction>)> {
    let value = serde_json::from_str::<Value>(line).ok()?;
    let kind = value.get("kind")?.as_str()?;
    if !matches!(
        kind,
        "startPumpfunExitWatch"
            | "updatePumpfunExitWatch"
            | "cancelPumpfunExitWatch"
            | "sourceFlatPumpfunExitWatch"
            | "sourceSellPumpfunExitWatch"
            | "activatePumpfunExitWatch"
            | "ackPumpfunExitSell"
    ) {
        return None;
    }
    let id = value
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();
    let mint = value
        .get("mint")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let command = match serde_json::from_value::<WatcherCommand>(value) {
        Ok(command) => command,
        Err(error) => {
            return Some((
                status_error(
                    id,
                    mint,
                    &format!("invalid Rust exit watcher command: {error}"),
                ),
                None,
            ))
        }
    };
    Some(match command {
        WatcherCommand::StartPumpfunExitWatch(cmd) => (registry.register(cmd), None),
        WatcherCommand::UpdatePumpfunExitWatch(cmd) => (registry.update(cmd), None),
        WatcherCommand::CancelPumpfunExitWatch(cmd) => (registry.cancel(cmd), None),
        WatcherCommand::SourceFlatPumpfunExitWatch(cmd) => registry.source_flat(cmd, now_ms),
        WatcherCommand::SourceSellPumpfunExitWatch(cmd) => registry.source_sell(cmd, now_ms),
        WatcherCommand::ActivatePumpfunExitWatch(cmd) => registry.activate(cmd, now_ms),
        WatcherCommand::AckPumpfunExitSell(cmd) => registry.ack(cmd),
    })
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CurveState {
    pub virtual_token_reserves: u64,
    pub virtual_sol_reserves: u64,
    pub complete: bool,
}

pub fn decode_curve_state(data: &[u8]) -> Option<CurveState> {
    if data.len() < 49 {
        return None;
    }
    Some(CurveState {
        virtual_token_reserves: read_u64(data, 8)?,
        virtual_sol_reserves: read_u64(data, 16)?,
        complete: data[48] == 1,
    })
}

fn read_u64(data: &[u8], offset: usize) -> Option<u64> {
    let mut value = [0_u8; 8];
    value.copy_from_slice(data.get(offset..offset + 8)?);
    Some(u64::from_le_bytes(value))
}

pub fn quote_sell_lamports(state: CurveState, tokens_in_raw: u64) -> Option<u64> {
    quote_sell_lamports_with_fee_bps(state, tokens_in_raw, DEFAULT_PUMP_FUN_SELL_TRADE_FEE_BPS)
}

pub fn quote_sell_lamports_with_fee_bps(
    state: CurveState,
    tokens_in_raw: u64,
    trade_fee_bps: u64,
) -> Option<u64> {
    if state.complete
        || tokens_in_raw == 0
        || state.virtual_token_reserves == 0
        || state.virtual_sol_reserves == 0
    {
        return None;
    }
    let pre_sol = state.virtual_sol_reserves as u128;
    let pre_tokens = state.virtual_token_reserves as u128;
    let post_tokens = pre_tokens.checked_add(tokens_in_raw as u128)?;
    let invariant = pre_sol.checked_mul(pre_tokens)?;
    let post_sol = invariant / post_tokens;
    let gross = pre_sol.checked_sub(post_sol)?;
    let fee = gross.checked_mul(trade_fee_bps.min(10_000) as u128)? / 10_000;
    u64::try_from(gross.checked_sub(fee)?).ok()
}

pub fn fee_adjusted_pnl_pct(
    expected_sol_out_lamports: u64,
    cost_basis_lamports: u64,
    execution_fee_lamports: u64,
) -> f64 {
    if cost_basis_lamports == 0 {
        return 0.0;
    }
    let net = expected_sol_out_lamports.saturating_sub(execution_fee_lamports);
    pnl_pct(net, cost_basis_lamports)
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExitMark {
    pub kind: &'static str,
    pub mint: String,
    pub bonding_curve: String,
    pub source: &'static str,
    pub phase: ExitPhase,
    pub slot: u64,
    pub observed_at_ms: u64,
    pub tokens_held_raw: u64,
    pub executable_sol_out_lamports: u64,
    pub net_sol_out_lamports: u64,
    pub remaining_cost_lamports: u64,
    pub pnl_pct: f64,
    pub peak_net_sol_out_lamports: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tp_6003_salvage: Option<Tp6003SalvageMark>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sell_status_unknown: Option<SellStatusUnknownMark>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Tp6003SalvageMark {
    pub mint: String,
    pub failure_at_ms: u64,
    pub failure_slot: Option<u64>,
    pub original_tp_sell_signature: Option<String>,
    pub current_authority_source: &'static str,
    pub current_authority_timestamp: u64,
    pub current_authority_slot: u64,
    pub authority_age_ms: u64,
    pub executable_pnl: f64,
    pub salvage_floor: f64,
    pub decision: &'static str,
    pub rejection_reason: Option<&'static str>,
    pub retry_number: u64,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SellStatusUnknownMark {
    pub mint: String,
    pub signature: String,
    pub original_exit_reason: ExitReason,
    pub sell_sent_at_ms: u64,
    pub status_timeout_at_ms: u64,
    pub duration_unknown_ms: u64,
    pub wallet_amount: u64,
    pub wallet_observation_at_ms: u64,
    pub signature_state: &'static str,
    pub decision: &'static str,
}

#[derive(Debug, Clone)]
pub struct ExitAction {
    pub mint: String,
    pub source_wallet: String,
    pub bonding_curve: String,
    pub sequence: u64,
    pub reason: ExitReason,
    pub phase_before: ExitPhase,
    pub phase_after_success: ExitPhase,
    pub tokens_in_raw: u64,
    pub expected_sol_out_lamports: u64,
    pub min_sol_out_lamports: u64,
    pub cost_basis_lamports: u64,
    pub decision_execution_fee_lamports: u64,
    pub pnl_pct: f64,
    pub gross_pnl_pct: f64,
    pub fee_adjusted_pnl_pct: f64,
    pub min_acceptable_take_profit_pnl_pct: Option<f64>,
    pub min_acceptable_take_profit_sol_out_lamports: Option<u64>,
    pub normal_take_profit_capture_slippage_bps: Option<u64>,
    pub tp_6003_salvage_min_realized_pnl_pct: Option<f64>,
    pub trigger_mark_source: &'static str,
    pub trigger_mark_slot: u64,
    pub trigger_mark_observed_at_ms: u64,
    pub trigger_mark_pnl_pct: f64,
    pub hard_stop_threshold_pnl_pct: Option<f64>,
    pub min_sol_out_mode: &'static str,
    pub exact_balance_proof: bool,
    pub safe_sell_tokens_raw: Option<u64>,
    pub shadow: bool,
    pub sell: PumpfunSellTemplate,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExitStatusLine {
    pub kind: &'static str,
    pub id: String,
    pub ok: bool,
    pub mint: String,
    pub phase: Option<ExitPhase>,
    pub error: Option<String>,
    /// True when the command caused (or found) a sell that is waiting for the
    /// Node acknowledgement.  Node must only suppress its fallback when this
    /// is true; an `ok` command response alone does not mean an exit happened.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub action_pending: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExitRouteFallbackLine {
    pub kind: &'static str,
    pub mint: String,
    pub bonding_curve: String,
    pub reason: &'static str,
    pub route: &'static str,
}

#[derive(Debug, Clone)]
struct PendingExit {
    action: ExitAction,
    status_unknown: Option<SellStatusUnknownState>,
}

#[derive(Debug, Clone)]
struct SellStatusUnknownState {
    signature: String,
    sell_sent_at_ms: u64,
    status_timeout_at_ms: u64,
}

#[derive(Debug, Clone)]
struct DeferredBalanceUpdate {
    tokens_held_raw: u64,
    cost_basis_lamports: u64,
    exact_proof: bool,
}

#[derive(Debug, Clone)]
struct RecentSourceSell {
    signature: String,
    observed_at_ms: u64,
}

#[derive(Debug, Clone)]
struct Tp6003SalvageState {
    failure_at_ms: u64,
    failure_slot: Option<u64>,
    original_tp_sell_signature: Option<String>,
    authoritative_inventory_amount_raw: u64,
    exact_balance_proof: bool,
    salvage_floor_pct: f64,
    retry_count: u64,
    original_tp_sequence: u64,
    latest_failure_sequence: u64,
    armed_at_ms: u64,
    entry_ts_ms: u64,
}

#[derive(Debug, Clone)]
struct WatchedPosition {
    mint: String,
    source_wallet: String,
    bonding_curve: String,
    entry_ts_ms: u64,
    remaining_tokens_raw: u64,
    safe_sell_tokens_raw: Option<u64>,
    remaining_cost_lamports: u64,
    buy_confirmed: bool,
    exact_balance_proof: bool,
    pre_proof_tp_only: bool,
    mark_telemetry_ms: u64,
    last_mark_telemetry_ms: u64,
    thresholds: ExitThresholds,
    sell: PumpfunSellTemplate,
    shadow: bool,
    phase: ExitPhase,
    tp_taken: bool,
    pending_source_sell: bool,
    source_sell_seen: bool,
    seen_source_signatures: std::collections::HashSet<String>,
    source_flat: bool,
    latest_curve: Option<CurveState>,
    latest_mark: Option<ExitMark>,
    peak_net_sol_out_lamports: u64,
    sequence: u64,
    pending_exit: Option<PendingExit>,
    deferred_balance_update: Option<DeferredBalanceUpdate>,
    next_exit_retry_after_ms: u64,
    tp_6003_salvage: Option<Tp6003SalvageState>,
}

impl WatchedPosition {
    fn new(cmd: StartPumpfunExitWatchCommand) -> Result<Self, String> {
        if cmd.mint.trim().is_empty() || cmd.bonding_curve.trim().is_empty() {
            return Err("mint and bondingCurve are required".to_string());
        }
        if cmd.tokens_held_raw == 0 || cmd.cost_basis_lamports == 0 {
            return Err("tokensHeldRaw and costBasisLamports must be positive".to_string());
        }
        if !cmd.thresholds.valid() {
            return Err("invalid Rust exit watcher thresholds".to_string());
        }
        if cmd.sell.accounts.is_empty() {
            return Err("canonical Pump.fun sell accounts are required".to_string());
        }
        Ok(Self {
            mint: cmd.mint,
            source_wallet: cmd.source_wallet,
            bonding_curve: cmd.bonding_curve,
            entry_ts_ms: cmd.entry_ts_ms,
            remaining_tokens_raw: cmd.tokens_held_raw,
            safe_sell_tokens_raw: cmd.safe_sell_tokens_raw,
            remaining_cost_lamports: cmd.cost_basis_lamports,
            buy_confirmed: cmd.buy_confirmed,
            exact_balance_proof: cmd.exact_balance_proof,
            pre_proof_tp_only: cmd.pre_proof_tp_only,
            mark_telemetry_ms: cmd.mark_telemetry_ms.max(100),
            last_mark_telemetry_ms: 0,
            thresholds: cmd.thresholds,
            sell: cmd.sell,
            shadow: cmd.shadow,
            phase: ExitPhase::OpenGrace,
            tp_taken: false,
            pending_source_sell: false,
            source_sell_seen: false,
            seen_source_signatures: std::collections::HashSet::new(),
            source_flat: false,
            latest_curve: None,
            latest_mark: None,
            peak_net_sol_out_lamports: 0,
            sequence: 0,
            pending_exit: None,
            deferred_balance_update: None,
            next_exit_retry_after_ms: 0,
            tp_6003_salvage: None,
        })
    }

    fn mark(&mut self, state: CurveState, slot: u64, now_ms: u64) -> Option<ExitAction> {
        self.mark_from_source(state, slot, now_ms, "unknown")
    }

    fn mark_from_source(
        &mut self,
        state: CurveState,
        slot: u64,
        now_ms: u64,
        source: &'static str,
    ) -> Option<ExitAction> {
        self.latest_curve = Some(state);
        if state.complete {
            self.phase = ExitPhase::Graduated;
            return None;
        }
        if self.phase == ExitPhase::OpenGrace
            && now_ms.saturating_sub(self.entry_ts_ms) >= self.thresholds.stop_loss_grace_ms
        {
            self.phase = ExitPhase::Open;
        }
        let executable = quote_sell_lamports_with_fee_bps(
            state,
            self.remaining_tokens_raw,
            self.thresholds.sell_trade_fee_bps,
        )?;
        let net = executable.saturating_sub(self.thresholds.estimated_execution_fee_lamports);
        let pnl_pct = pnl_pct(net, self.remaining_cost_lamports);
        self.peak_net_sol_out_lamports = self.peak_net_sol_out_lamports.max(net);
        let tp_6003_salvage = self.tp_6003_salvage_mark(source, slot, now_ms, pnl_pct);
        let sell_status_unknown = self.sell_status_unknown_mark(now_ms);
        self.latest_mark = Some(ExitMark {
            kind: "rustExitMark",
            mint: self.mint.clone(),
            bonding_curve: self.bonding_curve.clone(),
            source,
            phase: self.phase,
            slot,
            observed_at_ms: now_ms,
            tokens_held_raw: self.remaining_tokens_raw,
            executable_sol_out_lamports: executable,
            net_sol_out_lamports: net,
            remaining_cost_lamports: self.remaining_cost_lamports,
            pnl_pct,
            peak_net_sol_out_lamports: self.peak_net_sol_out_lamports,
            tp_6003_salvage,
            sell_status_unknown,
        });
        self.evaluate(now_ms)
    }

    fn mark_without_evaluate(&mut self, state: CurveState, slot: u64, now_ms: u64) -> Option<()> {
        self.mark_without_evaluate_from_source(state, slot, now_ms, "unknown")
    }

    fn mark_without_evaluate_from_source(
        &mut self,
        state: CurveState,
        slot: u64,
        now_ms: u64,
        source: &'static str,
    ) -> Option<()> {
        self.latest_curve = Some(state);
        if state.complete {
            self.phase = ExitPhase::Graduated;
            return None;
        }
        if self.phase == ExitPhase::OpenGrace
            && now_ms.saturating_sub(self.entry_ts_ms) >= self.thresholds.stop_loss_grace_ms
        {
            self.phase = ExitPhase::Open;
        }
        let executable = quote_sell_lamports_with_fee_bps(
            state,
            self.remaining_tokens_raw,
            self.thresholds.sell_trade_fee_bps,
        )?;
        let net = executable.saturating_sub(self.thresholds.estimated_execution_fee_lamports);
        let pnl_pct = pnl_pct(net, self.remaining_cost_lamports);
        self.peak_net_sol_out_lamports = self.peak_net_sol_out_lamports.max(net);
        let tp_6003_salvage = self.tp_6003_salvage_mark(source, slot, now_ms, pnl_pct);
        let sell_status_unknown = self.sell_status_unknown_mark(now_ms);
        self.latest_mark = Some(ExitMark {
            kind: "rustExitMark",
            mint: self.mint.clone(),
            bonding_curve: self.bonding_curve.clone(),
            source,
            phase: self.phase,
            slot,
            observed_at_ms: now_ms,
            tokens_held_raw: self.remaining_tokens_raw,
            executable_sol_out_lamports: executable,
            net_sol_out_lamports: net,
            remaining_cost_lamports: self.remaining_cost_lamports,
            pnl_pct,
            peak_net_sol_out_lamports: self.peak_net_sol_out_lamports,
            tp_6003_salvage,
            sell_status_unknown,
        });
        Some(())
    }

    fn sell_status_unknown_mark(&self, now_ms: u64) -> Option<SellStatusUnknownMark> {
        let pending = self.pending_exit.as_ref()?;
        let unknown = pending.status_unknown.as_ref()?;
        Some(SellStatusUnknownMark {
            mint: self.mint.clone(),
            signature: unknown.signature.clone(),
            original_exit_reason: pending.action.reason,
            sell_sent_at_ms: unknown.sell_sent_at_ms,
            status_timeout_at_ms: unknown.status_timeout_at_ms,
            duration_unknown_ms: now_ms.saturating_sub(unknown.status_timeout_at_ms),
            wallet_amount: self.remaining_tokens_raw,
            wallet_observation_at_ms: now_ms,
            signature_state: "unresolved",
            decision: "SELL_SUPPRESSED_STATUS_UNKNOWN",
        })
    }

    fn tp_6003_salvage_mark(
        &self,
        source: &'static str,
        slot: u64,
        now_ms: u64,
        pnl_pct: f64,
    ) -> Option<Tp6003SalvageMark> {
        let salvage = self.tp_6003_salvage.as_ref()?;
        let (decision, rejection_reason) =
            self.tp_6003_salvage_decision(source, now_ms, now_ms, pnl_pct, salvage);
        Some(Tp6003SalvageMark {
            mint: self.mint.clone(),
            failure_at_ms: salvage.failure_at_ms,
            failure_slot: salvage.failure_slot,
            original_tp_sell_signature: salvage.original_tp_sell_signature.clone(),
            current_authority_source: source,
            current_authority_timestamp: now_ms,
            current_authority_slot: slot,
            authority_age_ms: now_ms.saturating_sub(now_ms),
            executable_pnl: pnl_pct,
            salvage_floor: salvage.salvage_floor_pct,
            decision,
            rejection_reason,
            retry_number: salvage.retry_count.saturating_add(1),
        })
    }

    fn tp_6003_salvage_decision(
        &self,
        source: &'static str,
        observed_at_ms: u64,
        now_ms: u64,
        pnl_pct: f64,
        salvage: &Tp6003SalvageState,
    ) -> (&'static str, Option<&'static str>) {
        if self.pending_exit.is_some() {
            return ("SELL_ALREADY_PENDING", Some("sell_already_pending"));
        }
        if !self.exact_balance_proof || !salvage.exact_balance_proof {
            return (
                "WAITING_FRESH_AUTHORITY",
                Some("inventory_not_authoritative"),
            );
        }
        if source != "yellowstone-cache" {
            return ("WAITING_FRESH_AUTHORITY", Some("wrong_authority_source"));
        }
        if observed_at_ms <= salvage.failure_at_ms {
            return ("QUOTE_PRE_FAILURE", Some("quote_not_after_failure"));
        }
        if now_ms.saturating_sub(observed_at_ms)
            > self.thresholds.tp_6003_salvage_authority_max_age_ms
        {
            return ("QUOTE_STALE", Some("quote_stale"));
        }
        if pnl_pct < salvage.salvage_floor_pct {
            return ("BELOW_SALVAGE_FLOOR", Some("salvage_below_floor"));
        }
        ("RETRY_AUTHORIZED", None)
    }

    fn source_sell(&mut self, now_ms: u64) -> Option<ExitAction> {
        self.source_sell_seen = true;
        self.pending_source_sell = true;
        // Source sells can arrive before the next curve-account telemetry
        // event. Reuse the latest local curve state so the emergency branch
        // is evaluated immediately instead of waiting for another mark.
        if self.latest_mark.is_none() {
            if let Some(state) = self.latest_curve {
                return self.mark(state, 0, now_ms);
            }
        }
        self.evaluate(now_ms)
    }

    fn source_flat(&mut self, now_ms: u64) -> Option<ExitAction> {
        self.source_flat = true;
        self.pending_source_sell = true;
        if self.latest_mark.is_none() {
            if let Some(state) = self.latest_curve {
                return self.mark(state, 0, now_ms);
            }
        }
        self.evaluate(now_ms)
    }

    fn evaluate(&mut self, now_ms: u64) -> Option<ExitAction> {
        if !self.buy_confirmed {
            return None;
        }
        if self.pending_exit.is_some()
            || matches!(
                self.phase,
                ExitPhase::ExitFailedRecoverable | ExitPhase::Closed | ExitPhase::Graduated
            )
        {
            return None;
        }
        if now_ms < self.next_exit_retry_after_ms {
            return None;
        }
        let mark = self.latest_mark.clone()?;
        let age_ms = now_ms.saturating_sub(self.entry_ts_ms);

        let decision = if let Some(salvage) = self.tp_6003_salvage.as_ref() {
            if self.source_flat {
                Some((ExitReason::SourceFlat, 100.0, ExitPhase::Closed, None))
            } else if self.thresholds.max_hold_ms > 0 && age_ms >= self.thresholds.max_hold_ms {
                Some((ExitReason::MaxHold, 100.0, ExitPhase::Closed, None))
            } else if age_ms >= self.thresholds.stop_loss_grace_ms
                && mark.pnl_pct <= -self.thresholds.stop_loss_pct
            {
                Some((ExitReason::HardStopLoss, 100.0, ExitPhase::Closed, None))
            } else {
                let (salvage_decision, _) = self.tp_6003_salvage_decision(
                    mark.source,
                    mark.observed_at_ms,
                    now_ms,
                    mark.pnl_pct,
                    salvage,
                );
                if salvage_decision == "RETRY_AUTHORIZED" {
                    Some((
                        ExitReason::TakeProfit,
                        self.thresholds.take_profit_sell_pct,
                        ExitPhase::Runner,
                        Some(salvage.salvage_floor_pct),
                    ))
                } else {
                    None
                }
            }
        } else if self.source_flat {
            Some((ExitReason::SourceFlat, 100.0, ExitPhase::Closed, None))
        } else if self.source_sell_seen && self.phase == ExitPhase::Runner {
            Some((ExitReason::RunnerSourceSell, 100.0, ExitPhase::Closed, None))
        } else if self.pending_source_sell
            && age_ms < self.thresholds.early_source_window_ms
            && mark.pnl_pct < self.thresholds.early_source_max_pnl_pct
        {
            Some((ExitReason::EarlySourceSell, 100.0, ExitPhase::Closed, None))
        } else if self.pending_source_sell && mark.pnl_pct < 0.0 {
            Some((ExitReason::LosingSourceSell, 100.0, ExitPhase::Closed, None))
        } else if self.pending_source_sell
            && mark.pnl_pct >= 0.0
            && mark.pnl_pct < self.thresholds.early_source_max_pnl_pct
        {
            Some((
                ExitReason::SourcePartial,
                self.thresholds.source_partial_sell_pct,
                ExitPhase::Open,
                None,
            ))
        } else if !self.tp_taken && mark.pnl_pct >= self.thresholds.take_profit_pct {
            Some((
                ExitReason::TakeProfit,
                self.thresholds.take_profit_sell_pct,
                ExitPhase::Runner,
                None,
            ))
        } else if self.phase == ExitPhase::Runner
            && runner_drawdown_hit(
                mark.net_sol_out_lamports,
                self.peak_net_sol_out_lamports,
                self.thresholds.runner_drawdown_pct,
            )
        {
            Some((ExitReason::RunnerDrawdown, 100.0, ExitPhase::Closed, None))
        } else if self.thresholds.max_hold_ms > 0 && age_ms >= self.thresholds.max_hold_ms {
            Some((ExitReason::MaxHold, 100.0, ExitPhase::Closed, None))
        } else if age_ms >= self.thresholds.stop_loss_grace_ms
            && mark.pnl_pct <= -self.thresholds.stop_loss_pct
        {
            Some((ExitReason::HardStopLoss, 100.0, ExitPhase::Closed, None))
        } else {
            None
        };

        if self.pending_source_sell && decision.is_none() {
            self.pending_source_sell = false;
        }
        let (reason, sell_pct, phase_after_success, min_pnl_override) = decision?;
        let action = self.build_action(reason, sell_pct, phase_after_success, min_pnl_override);
        if action.is_some() && min_pnl_override.is_some() {
            if let Some(salvage) = self.tp_6003_salvage.as_mut() {
                salvage.retry_count = salvage.retry_count.saturating_add(1);
            }
        }
        action
    }

    fn build_action(
        &mut self,
        reason: ExitReason,
        sell_pct: f64,
        phase_after_success: ExitPhase,
        min_acceptable_take_profit_pnl_pct_override: Option<f64>,
    ) -> Option<ExitAction> {
        let mark = self.latest_mark.clone()?;
        if self.pre_proof_tp_only
            && !self.exact_balance_proof
            && !matches!(reason, ExitReason::TakeProfit)
        {
            return None;
        }
        let mut tokens = percent_of(self.remaining_tokens_raw, sell_pct);
        if !self.exact_balance_proof {
            if let Some(safe) = self.safe_sell_tokens_raw.filter(|value| *value > 0) {
                tokens = tokens.min(safe).max(1);
            }
        }
        let state = self.latest_curve?;
        let expected =
            quote_sell_lamports_with_fee_bps(state, tokens, self.thresholds.sell_trade_fee_bps)?;
        let proportional_cost = proportional_cost_lamports(
            self.remaining_cost_lamports,
            self.remaining_tokens_raw,
            tokens,
        );
        let decision_execution_fee_lamports = self
            .thresholds
            .decision_execution_fee_lamports
            .unwrap_or(self.thresholds.estimated_execution_fee_lamports);
        let gross_pnl_pct = pnl_pct(expected, proportional_cost);
        let fee_adjusted_pnl_pct =
            fee_adjusted_pnl_pct(expected, proportional_cost, decision_execution_fee_lamports);
        let min_acceptable_take_profit_pnl_pct = min_acceptable_take_profit_pnl_pct_override;
        let min_acceptable_take_profit_sol_out_lamports =
            if matches!(reason, ExitReason::TakeProfit) {
                min_acceptable_take_profit_pnl_pct.and_then(|pnl| {
                    min_sol_out_for_fee_adjusted_pnl(
                        proportional_cost,
                        decision_execution_fee_lamports,
                        pnl,
                    )
                })
            } else {
                None
            };
        if matches!(reason, ExitReason::TakeProfit) {
            if let Some(min_pnl) = min_acceptable_take_profit_pnl_pct {
                if fee_adjusted_pnl_pct < min_pnl {
                    return None;
                }
            }
        }
        let force_full_close = sell_pct >= 100.0
            && matches!(
                reason,
                ExitReason::SourceFlat
                    | ExitReason::EarlySourceSell
                    | ExitReason::LosingSourceSell
                    | ExitReason::RunnerSourceSell
                    | ExitReason::RunnerDrawdown
                    | ExitReason::HardStopLoss
                    | ExitReason::MaxHold
            );
        let (min, min_sol_out_mode) =
            if force_full_close {
                // Emergency exits are risk-off. A stale tight min-out can strand dust
                // positions while price keeps moving away from us.
                (1, "risk_off_floor")
            } else if matches!(reason, ExitReason::TakeProfit) {
                if let Some(floor) = min_acceptable_take_profit_sol_out_lamports {
                    (floor.max(1), "tp_6003_salvage_min_realized_pnl")
                } else {
                    (
                        expected.saturating_mul(10_000_u64.saturating_sub(
                            self.thresholds.normal_take_profit_capture_slippage_bps,
                        )) / 10_000,
                        "normal_take_profit_capture",
                    )
                }
            } else {
                (
                    expected.saturating_mul(
                        10_000_u64.saturating_sub(self.thresholds.sell_slippage_bps),
                    ) / 10_000,
                    "quote_slippage",
                )
            };
        self.sequence = self.sequence.saturating_add(1);
        let action = ExitAction {
            mint: self.mint.clone(),
            source_wallet: self.source_wallet.clone(),
            bonding_curve: self.bonding_curve.clone(),
            sequence: self.sequence,
            reason,
            phase_before: self.phase,
            phase_after_success,
            tokens_in_raw: tokens,
            expected_sol_out_lamports: expected,
            min_sol_out_lamports: min,
            cost_basis_lamports: proportional_cost,
            decision_execution_fee_lamports,
            pnl_pct: mark.pnl_pct,
            gross_pnl_pct,
            fee_adjusted_pnl_pct,
            min_acceptable_take_profit_pnl_pct,
            min_acceptable_take_profit_sol_out_lamports,
            normal_take_profit_capture_slippage_bps: matches!(reason, ExitReason::TakeProfit)
                .then_some(self.thresholds.normal_take_profit_capture_slippage_bps),
            tp_6003_salvage_min_realized_pnl_pct: matches!(reason, ExitReason::TakeProfit)
                .then_some(self.thresholds.tp_6003_salvage_min_realized_pnl_pct)
                .flatten(),
            trigger_mark_source: mark.source,
            trigger_mark_slot: mark.slot,
            trigger_mark_observed_at_ms: mark.observed_at_ms,
            trigger_mark_pnl_pct: mark.pnl_pct,
            hard_stop_threshold_pnl_pct: matches!(reason, ExitReason::HardStopLoss)
                .then_some(-self.thresholds.stop_loss_pct),
            min_sol_out_mode,
            exact_balance_proof: self.exact_balance_proof,
            safe_sell_tokens_raw: self.safe_sell_tokens_raw,
            shadow: self.shadow,
            sell: self.sell.clone(),
        };
        self.phase = ExitPhase::Exiting;
        self.pending_exit = Some(PendingExit {
            action: action.clone(),
            status_unknown: None,
        });
        Some(action)
    }

    fn mark_sell_status_unknown(
        &mut self,
        sequence: u64,
        signature: String,
        sell_sent_at_ms: u64,
        status_timeout_at_ms: u64,
    ) -> Result<(), String> {
        let pending = self
            .pending_exit
            .as_mut()
            .ok_or_else(|| "no Rust exit sell is pending".to_string())?;
        if pending.action.sequence != sequence {
            return Err("Rust exit sell sequence mismatch".to_string());
        }
        pending.status_unknown = Some(SellStatusUnknownState {
            signature,
            sell_sent_at_ms,
            status_timeout_at_ms,
        });
        self.phase = ExitPhase::SellSentStatusUnknown;
        self.next_exit_retry_after_ms = u64::MAX;
        Ok(())
    }

    fn cancel_pending_hard_stop_revalidation(
        &mut self,
        sequence: u64,
        authoritative_state: Option<(CurveState, u64, u64, &'static str)>,
    ) -> Result<(), String> {
        let pending = self
            .pending_exit
            .take()
            .ok_or_else(|| "no Rust exit sell is pending".to_string())?;
        if pending.action.sequence != sequence {
            self.pending_exit = Some(pending);
            return Err("Rust exit sell sequence mismatch".to_string());
        }
        if pending.action.reason != ExitReason::HardStopLoss {
            self.pending_exit = Some(pending);
            return Err("pending Rust exit sell is not a hard stop".to_string());
        }
        self.phase = pending.action.phase_before;
        self.next_exit_retry_after_ms = 0;
        if let Some((state, slot, now_ms, source)) = authoritative_state {
            let _ = self.mark_without_evaluate_from_source(state, slot, now_ms, source);
        }
        Ok(())
    }

    fn ack(
        &mut self,
        sequence: u64,
        ok: bool,
        _actual_sol_out: Option<u64>,
    ) -> Result<Option<ExitAction>, String> {
        self.ack_with_retry(sequence, ok, false)
    }

    fn ack_with_retry(
        &mut self,
        sequence: u64,
        ok: bool,
        retry_immediately: bool,
    ) -> Result<Option<ExitAction>, String> {
        let pending = self
            .pending_exit
            .take()
            .ok_or_else(|| "no Rust exit sell is pending".to_string())?;
        if pending.action.sequence != sequence {
            self.pending_exit = Some(pending);
            return Err("Rust exit sell sequence mismatch".to_string());
        }
        if !ok {
            let pending_pre_exact_safe_sell = !pending.action.exact_balance_proof
                && pending.action.safe_sell_tokens_raw.is_some();
            if pending_pre_exact_safe_sell {
                if let Some(deferred) = self.deferred_balance_update.take() {
                    self.apply_balance_update(
                        deferred.tokens_held_raw,
                        deferred.cost_basis_lamports,
                        deferred.exact_proof,
                    );
                    if matches!(self.phase, ExitPhase::Exiting) {
                        let mark_age_ms = self
                            .latest_mark
                            .as_ref()
                            .map(|mark| mark.observed_at_ms.saturating_sub(self.entry_ts_ms))
                            .unwrap_or_default();
                        self.phase = if mark_age_ms >= self.thresholds.stop_loss_grace_ms {
                            ExitPhase::Open
                        } else {
                            ExitPhase::OpenGrace
                        };
                    }
                    self.next_exit_retry_after_ms = 0;
                    return Ok(None);
                }
            }
            self.phase = pending.action.phase_before;
            self.next_exit_retry_after_ms = if retry_immediately {
                0
            } else {
                self.latest_mark
                    .as_ref()
                    .map(|mark| mark.observed_at_ms)
                    .unwrap_or_default()
                    .saturating_add(FAILED_EXIT_RETRY_COOLDOWN_MS)
            };
            return Ok(None);
        }

        let sold = pending.action.tokens_in_raw.min(self.remaining_tokens_raw);
        let old_tokens = self.remaining_tokens_raw;
        let sold_cost = if sold == old_tokens {
            self.remaining_cost_lamports
        } else {
            ((self.remaining_cost_lamports as u128 * sold as u128) / old_tokens as u128) as u64
        };
        self.remaining_tokens_raw = self.remaining_tokens_raw.saturating_sub(sold);
        self.remaining_cost_lamports = self.remaining_cost_lamports.saturating_sub(sold_cost);
        if matches!(
            pending.action.reason,
            ExitReason::SourceFlat
                | ExitReason::EarlySourceSell
                | ExitReason::LosingSourceSell
                | ExitReason::SourcePartial
                | ExitReason::RunnerSourceSell
        ) {
            self.pending_source_sell = false;
        }
        let was_tp_6003_salvage_exit = self.tp_6003_salvage.is_some()
            && pending.action.reason == ExitReason::TakeProfit
            && pending.action.min_acceptable_take_profit_pnl_pct.is_some();
        if pending.action.reason == ExitReason::TakeProfit {
            self.tp_taken = true;
        }
        if self.remaining_tokens_raw == 0
            || was_tp_6003_salvage_exit
            || matches!(
                pending.action.reason,
                ExitReason::SourceFlat
                    | ExitReason::EarlySourceSell
                    | ExitReason::LosingSourceSell
                    | ExitReason::RunnerSourceSell
                    | ExitReason::RunnerDrawdown
                    | ExitReason::HardStopLoss
                    | ExitReason::MaxHold
            )
        {
            self.tp_6003_salvage = None;
        }
        self.phase = if self.remaining_tokens_raw == 0 {
            ExitPhase::Closed
        } else {
            pending.action.phase_after_success
        };
        self.next_exit_retry_after_ms = 0;
        self.peak_net_sol_out_lamports = 0;
        let follow_up =
            if let (Some(state), Some(mark)) = (self.latest_curve, self.latest_mark.as_ref()) {
                self.mark(state, mark.slot, mark.observed_at_ms)
            } else {
                None
            };
        Ok(follow_up)
    }

    fn arm_tp_6003_salvage(
        &mut self,
        sequence: u64,
        failure_at_ms: u64,
        failure_slot: Option<u64>,
        original_tp_sell_signature: Option<String>,
        salvage_floor_pct: f64,
    ) -> Result<Option<ExitAction>, String> {
        let pending = self
            .pending_exit
            .take()
            .ok_or_else(|| "no Rust TP6003 sell is pending".to_string())?;
        if pending.action.sequence != sequence {
            self.pending_exit = Some(pending);
            return Err("Rust exit sell sequence mismatch".to_string());
        }
        if pending.action.reason != ExitReason::TakeProfit {
            self.pending_exit = Some(pending);
            return Err("pending Rust exit sell is not a take-profit".to_string());
        }
        if !pending.action.exact_balance_proof || pending.action.tokens_in_raw == 0 {
            self.pending_exit = Some(pending);
            return Err("TP6003 salvage requires exact authoritative inventory".to_string());
        }
        let previous_retry_count = self
            .tp_6003_salvage
            .as_ref()
            .map(|state| state.retry_count)
            .unwrap_or(0);
        self.tp_6003_salvage = Some(Tp6003SalvageState {
            failure_at_ms,
            failure_slot,
            original_tp_sell_signature,
            authoritative_inventory_amount_raw: pending.action.tokens_in_raw,
            exact_balance_proof: pending.action.exact_balance_proof,
            salvage_floor_pct,
            retry_count: previous_retry_count,
            original_tp_sequence: pending.action.sequence,
            latest_failure_sequence: pending.action.sequence,
            armed_at_ms: failure_at_ms,
            entry_ts_ms: self.entry_ts_ms,
        });
        self.phase = ExitPhase::Tp6003SalvageArmed;
        self.next_exit_retry_after_ms = 0;
        Ok(self.evaluate(failure_at_ms))
    }

    fn retry_profit_6003_now(
        &mut self,
        sequence: u64,
        state: CurveState,
        slot: u64,
        now_ms: u64,
        min_acceptable_take_profit_pnl_pct: f64,
    ) -> Result<Option<ExitAction>, String> {
        self.arm_tp_6003_salvage(
            sequence,
            now_ms.saturating_sub(1),
            Some(slot),
            None,
            min_acceptable_take_profit_pnl_pct,
        )?;
        self.mark_without_evaluate_from_source(state, slot, now_ms, "yellowstone-cache");
        let action = self.build_action(
            ExitReason::TakeProfit,
            self.thresholds.take_profit_sell_pct,
            ExitPhase::Runner,
            Some(min_acceptable_take_profit_pnl_pct),
        );
        if action.is_some() {
            if let Some(salvage) = self.tp_6003_salvage.as_mut() {
                salvage.retry_count = salvage.retry_count.saturating_add(1);
            }
        }
        Ok(action)
    }

    fn fail_exit_recoverable(&mut self, sequence: u64) -> Result<(), String> {
        let pending = self
            .pending_exit
            .take()
            .ok_or_else(|| "no Rust exit sell is pending".to_string())?;
        if pending.action.sequence != sequence {
            self.pending_exit = Some(pending);
            return Err("Rust exit sell sequence mismatch".to_string());
        }
        self.phase = ExitPhase::ExitFailedRecoverable;
        self.next_exit_retry_after_ms = u64::MAX;
        Ok(())
    }

    fn apply_balance_update(
        &mut self,
        tokens_held_raw: u64,
        cost_basis_lamports: u64,
        exact_proof: bool,
    ) {
        self.buy_confirmed = true;
        if exact_proof {
            self.exact_balance_proof = true;
            self.safe_sell_tokens_raw = None;
        }
        if matches!(self.phase, ExitPhase::Closed | ExitPhase::Graduated) {
            self.phase = ExitPhase::Open;
            self.source_flat = true;
            self.pending_source_sell = true;
        }

        if self.remaining_tokens_raw != tokens_held_raw {
            self.remaining_tokens_raw = tokens_held_raw;
            if self.remaining_cost_lamports == 0 {
                self.remaining_cost_lamports = cost_basis_lamports.max(1);
            }
            self.latest_mark = None;
            self.peak_net_sol_out_lamports = 0;
        }
    }

    fn reconcile_wallet_balance(
        &mut self,
        tokens_held_raw: u64,
        cost_basis_lamports: u64,
        now_ms: u64,
    ) -> Option<ExitAction> {
        if tokens_held_raw == 0 {
            return None;
        }
        if self.pending_exit.is_some() {
            self.deferred_balance_update = Some(DeferredBalanceUpdate {
                tokens_held_raw,
                cost_basis_lamports,
                exact_proof: true,
            });
            self.buy_confirmed = true;
            self.exact_balance_proof = true;
            return None;
        }

        self.apply_balance_update(tokens_held_raw, cost_basis_lamports, true);

        if self.source_flat && self.latest_mark.is_none() {
            if let Some(state) = self.latest_curve {
                return self.mark(state, 0, now_ms);
            }
        }
        self.evaluate(now_ms)
    }
}

fn pnl_pct(net_proceeds: u64, cost: u64) -> f64 {
    if cost == 0 {
        return 0.0;
    }
    ((net_proceeds as f64 / cost as f64) - 1.0) * 100.0
}

fn min_sol_out_for_fee_adjusted_pnl(
    cost_lamports: u64,
    execution_fee_lamports: u64,
    pnl_pct: f64,
) -> Option<u64> {
    if cost_lamports == 0 || !pnl_pct.is_finite() || pnl_pct <= -100.0 {
        return None;
    }
    let required_before_fee = (cost_lamports as f64 * (1.0 + pnl_pct / 100.0)).ceil();
    if !required_before_fee.is_finite() || required_before_fee < 0.0 {
        return None;
    }
    let required = required_before_fee as u128 + execution_fee_lamports as u128;
    Some(required.min(u64::MAX as u128) as u64)
}

fn percent_of(value: u64, pct: f64) -> u64 {
    if pct >= 100.0 {
        return value;
    }
    (((value as f64 * pct / 100.0).floor() as u64).max(1)).min(value)
}

fn proportional_cost_lamports(total_cost: u64, total_tokens: u64, sold_tokens: u64) -> u64 {
    if total_tokens == 0 || sold_tokens >= total_tokens {
        return total_cost;
    }
    ((total_cost as u128 * sold_tokens as u128) / total_tokens as u128)
        .try_into()
        .unwrap_or(u64::MAX)
}

fn runner_drawdown_hit(current: u64, peak: u64, drawdown_pct: f64) -> bool {
    peak > 0 && (current as f64 / peak as f64 - 1.0) * 100.0 <= -drawdown_pct
}

#[derive(Debug, Clone, Default)]
pub struct ExitWatcherRegistry {
    positions: Arc<Mutex<HashMap<String, WatchedPosition>>>,
    recent_source_sells: Arc<Mutex<HashMap<String, Vec<RecentSourceSell>>>>,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ExitWatcherSessionCounts {
    pub open_positions: usize,
    pub pending_sells: usize,
    pub status_unknown_sells: usize,
    pub tp6003_salvage_positions: usize,
}

impl ExitWatcherRegistry {
    pub fn register(&self, cmd: StartPumpfunExitWatchCommand) -> ExitStatusLine {
        let id = cmd.id.clone();
        let mint = cmd.mint.clone();
        let position = match WatchedPosition::new(cmd) {
            Ok(position) => position,
            Err(error) => {
                return ExitStatusLine {
                    kind: "rustExitWatchResult",
                    id,
                    ok: false,
                    mint,
                    phase: None,
                    error: Some(error),
                    action_pending: Some(false),
                }
            }
        };
        let mut position = position;
        let cache_key = source_cache_key(&position.source_wallet, &mint);
        if let Some(recent) = self
            .recent_source_sells
            .lock()
            .expect("exit watcher source cache mutex poisoned")
            .remove(&cache_key)
        {
            for signal in recent {
                if signal.observed_at_ms >= position.entry_ts_ms
                    && position.seen_source_signatures.insert(signal.signature)
                {
                    let _ = position.source_sell(signal.observed_at_ms);
                }
            }
        }
        let phase = position.phase;
        self.positions
            .lock()
            .expect("exit watcher mutex poisoned")
            .insert(mint.clone(), position);
        ExitStatusLine {
            kind: "rustExitWatchResult",
            id,
            ok: true,
            mint,
            phase: Some(phase),
            error: None,
            action_pending: Some(false),
        }
    }

    pub fn update(&self, cmd: UpdatePumpfunExitWatchCommand) -> ExitStatusLine {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&cmd.mint) else {
            return status_error(cmd.id, cmd.mint, "Rust exit watch not found");
        };
        if position.pending_exit.is_some() {
            return status_error(
                cmd.id,
                cmd.mint,
                "cannot update while a Rust exit sell is pending",
            );
        }
        if cmd.tokens_held_raw == 0 || cmd.cost_basis_lamports == 0 {
            return status_error(cmd.id, cmd.mint, "updated tokens and cost must be positive");
        }
        position.remaining_tokens_raw = cmd.tokens_held_raw;
        position.remaining_cost_lamports = cmd.cost_basis_lamports;
        if cmd.exact_proof {
            position.exact_balance_proof = true;
            position.safe_sell_tokens_raw = None;
        }
        let _proof_source = cmd.proof_source;
        position.latest_mark = None;
        position.peak_net_sol_out_lamports = 0;
        status_ok(cmd.id, cmd.mint, position.phase)
    }

    pub fn reconcile_wallet_balance(
        &self,
        id: String,
        mint: String,
        tokens_held_raw: u64,
        cost_basis_lamports: u64,
        now_ms: u64,
    ) -> (ExitStatusLine, Option<ExitAction>) {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&mint) else {
            return (status_error(id, mint, "Rust exit watch not found"), None);
        };
        if tokens_held_raw == 0 || cost_basis_lamports == 0 {
            return (
                status_error(
                    id,
                    mint,
                    "wallet reconcile tokens and cost must be positive",
                ),
                None,
            );
        }
        let action =
            position.reconcile_wallet_balance(tokens_held_raw, cost_basis_lamports, now_ms);
        (
            status_with_action(id, mint, position.phase, action.is_some()),
            action,
        )
    }

    pub fn reconcile_wallet_flat(&self, id: String, mint: String) -> ExitStatusLine {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&mint) else {
            return status_error(id, mint, "Rust exit watch not found");
        };
        position.remaining_tokens_raw = 0;
        position.remaining_cost_lamports = 0;
        position.phase = ExitPhase::Closed;
        position.pending_exit = None;
        position.tp_6003_salvage = None;
        status_ok(id, mint, ExitPhase::Closed)
    }

    pub fn cancel(&self, cmd: CancelPumpfunExitWatchCommand) -> ExitStatusLine {
        if let Some(reason) = cmd.reason.as_deref() {
            eprintln!("rust-sidecar: exit watch {} cancelled: {reason}", cmd.mint);
        }
        let removed = self
            .positions
            .lock()
            .expect("exit watcher mutex poisoned")
            .remove(&cmd.mint);
        if removed.is_some() {
            status_ok(cmd.id, cmd.mint, ExitPhase::Closed)
        } else {
            status_error(cmd.id, cmd.mint, "Rust exit watch not found")
        }
    }

    pub fn source_flat(
        &self,
        cmd: SourceFlatPumpfunExitWatchCommand,
        now_ms: u64,
    ) -> (ExitStatusLine, Option<ExitAction>) {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&cmd.mint) else {
            return (
                status_error(cmd.id, cmd.mint, "Rust exit watch not found"),
                None,
            );
        };
        let action = position.source_flat(now_ms);
        (
            status_with_action(cmd.id, cmd.mint, position.phase, action.is_some()),
            action,
        )
    }

    pub fn source_sell(
        &self,
        cmd: SourceSellPumpfunExitWatchCommand,
        now_ms: u64,
    ) -> (ExitStatusLine, Option<ExitAction>) {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&cmd.mint) else {
            drop(positions);
            self.remember_source_sell(&cmd.source_wallet, &cmd.mint, &cmd.source_signature, now_ms);
            return (
                status_error(cmd.id, cmd.mint, "Rust exit watch not found"),
                None,
            );
        };
        if position.source_wallet != cmd.source_wallet {
            return (
                status_error(
                    cmd.id,
                    cmd.mint,
                    "source wallet does not own Rust exit watch",
                ),
                None,
            );
        }
        // The amount is carried for telemetry/correlation; source flatness is
        // deliberately determined from the cumulative ledger, not one sell.
        let _source_sell_amount_raw = cmd.sold_tokens_raw;
        if !position.seen_source_signatures.insert(cmd.source_signature) {
            return (
                status_with_action(
                    cmd.id,
                    cmd.mint,
                    position.phase,
                    position.pending_exit.is_some(),
                ),
                None,
            );
        }
        // WatchedPosition::source_sell marks the source signal first, then
        // revalues from the latest local curve state when needed. Keeping the
        // state transition in one place prevents a source sell from being
        // acknowledged without actually entering the emergency branch.
        // A source-flat signal has precedence over a partial source sell. It
        // means the leader has no remaining inventory, so the full position
        // must be closed even when the same transaction also carried a sell.
        let action = if cmd.source_flat {
            position.source_flat(now_ms)
        } else {
            position.source_sell(now_ms)
        };
        (
            status_with_action(
                cmd.id,
                cmd.mint,
                position.phase,
                action.is_some() || position.pending_exit.is_some(),
            ),
            action,
        )
    }

    pub fn activate(
        &self,
        cmd: ActivatePumpfunExitWatchCommand,
        now_ms: u64,
    ) -> (ExitStatusLine, Option<ExitAction>) {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&cmd.mint) else {
            return (
                status_error(cmd.id, cmd.mint, "Rust exit watch not found"),
                None,
            );
        };
        position.buy_confirmed = true;
        let action = if position.latest_mark.is_none() {
            position
                .latest_curve
                .and_then(|state| position.mark(state, 0, now_ms))
        } else {
            position.evaluate(now_ms)
        };
        (
            status_with_action(cmd.id, cmd.mint, position.phase, action.is_some()),
            action,
        )
    }

    pub fn ack(&self, cmd: AckPumpfunExitSellCommand) -> (ExitStatusLine, Option<ExitAction>) {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&cmd.mint) else {
            return (
                status_error(cmd.id, cmd.mint, "Rust exit watch not found"),
                None,
            );
        };
        if !cmd.ok {
            eprintln!(
                "rust-sidecar: exit sell {} sequence {} reopened: {}",
                cmd.mint,
                cmd.sequence,
                cmd.error.as_deref().unwrap_or("unspecified failure")
            );
        }
        match position.ack(cmd.sequence, cmd.ok, cmd.actual_sol_out_lamports) {
            Ok(action) => (
                status_with_action(cmd.id, cmd.mint, position.phase, action.is_some()),
                action,
            ),
            Err(error) => (status_error(cmd.id, cmd.mint, &error), None),
        }
    }

    pub fn mark_sell_status_unknown(
        &self,
        id: String,
        mint: String,
        sequence: u64,
        signature: String,
        sell_sent_at_ms: u64,
        status_timeout_at_ms: u64,
    ) -> ExitStatusLine {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&mint) else {
            return status_error(id, mint, "Rust exit watch not found");
        };
        match position.mark_sell_status_unknown(
            sequence,
            signature,
            sell_sent_at_ms,
            status_timeout_at_ms,
        ) {
            Ok(()) => status_with_action(id, mint, position.phase, true),
            Err(error) => status_error(id, mint, &error),
        }
    }

    pub fn cancel_pending_hard_stop_revalidation(
        &self,
        id: String,
        mint: String,
        sequence: u64,
        authoritative_state: Option<(CurveState, u64, u64, &'static str)>,
    ) -> ExitStatusLine {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&mint) else {
            return status_error(id, mint, "Rust exit watch not found");
        };
        match position.cancel_pending_hard_stop_revalidation(sequence, authoritative_state) {
            Ok(()) => status_with_action(id, mint, position.phase, false),
            Err(error) => status_error(id, mint, &error),
        }
    }

    pub fn retry_profit_6003_now(
        &self,
        id: String,
        mint: String,
        sequence: u64,
        state: CurveState,
        slot: u64,
        now_ms: u64,
        min_acceptable_take_profit_pnl_pct: f64,
    ) -> (ExitStatusLine, Option<ExitAction>) {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&mint) else {
            return (status_error(id, mint, "Rust exit watch not found"), None);
        };
        match position.retry_profit_6003_now(
            sequence,
            state,
            slot,
            now_ms,
            min_acceptable_take_profit_pnl_pct,
        ) {
            Ok(action) => (
                status_with_action(id, mint, position.phase, action.is_some()),
                action,
            ),
            Err(error) => (status_error(id, mint, &error), None),
        }
    }

    pub fn reopen_failed_exit_without_cooldown(
        &self,
        id: String,
        mint: String,
        sequence: u64,
    ) -> ExitStatusLine {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&mint) else {
            return status_error(id, mint, "Rust exit watch not found");
        };
        match position.ack_with_retry(sequence, false, true) {
            Ok(action) => status_with_action(id, mint, position.phase, action.is_some()),
            Err(error) => status_error(id, mint, &error),
        }
    }

    pub fn fail_exit_recoverable(&self, id: String, mint: String, sequence: u64) -> ExitStatusLine {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&mint) else {
            return status_error(id, mint, "Rust exit watch not found");
        };
        match position.fail_exit_recoverable(sequence) {
            Ok(()) => status_with_action(id, mint, position.phase, false),
            Err(error) => status_error(id, mint, &error),
        }
    }

    pub fn on_curve_account(
        &self,
        bonding_curve: &str,
        data: &[u8],
        slot: u64,
        now_ms: u64,
    ) -> (
        Option<ExitMark>,
        Option<ExitAction>,
        Option<ExitRouteFallbackLine>,
    ) {
        self.on_curve_account_with_source(
            bonding_curve,
            data,
            slot,
            now_ms,
            "yellowstone-exit-account",
        )
    }

    pub fn arm_tp_6003_salvage(
        &self,
        id: String,
        mint: String,
        sequence: u64,
        failure_at_ms: u64,
        failure_slot: Option<u64>,
        original_tp_sell_signature: Option<String>,
        salvage_floor_pct: f64,
    ) -> (ExitStatusLine, Option<ExitAction>) {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(&mint) else {
            return (status_error(id, mint, "Rust exit watch not found"), None);
        };
        match position.arm_tp_6003_salvage(
            sequence,
            failure_at_ms,
            failure_slot,
            original_tp_sell_signature,
            salvage_floor_pct,
        ) {
            Ok(action) => (
                status_with_action(id, mint, position.phase, action.is_some()),
                action,
            ),
            Err(error) => (status_error(id, mint, &error), None),
        }
    }

    pub fn on_curve_account_with_source(
        &self,
        bonding_curve: &str,
        data: &[u8],
        slot: u64,
        now_ms: u64,
        source: &'static str,
    ) -> (
        Option<ExitMark>,
        Option<ExitAction>,
        Option<ExitRouteFallbackLine>,
    ) {
        let Some(state) = decode_curve_state(data) else {
            return (None, None, None);
        };
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions
            .values_mut()
            .find(|position| position.bonding_curve == bonding_curve)
        else {
            return (None, None, None);
        };
        let action = position.mark_from_source(state, slot, now_ms, source);
        if position.phase == ExitPhase::Graduated {
            return (
                None,
                None,
                Some(ExitRouteFallbackLine {
                    kind: "rustExitRouteFallback",
                    mint: position.mint.clone(),
                    bonding_curve: position.bonding_curve.clone(),
                    reason: "pumpfun-curve-complete",
                    route: "pumpswap-canonical",
                }),
            );
        }
        let emit_mark = action.is_some()
            || now_ms.saturating_sub(position.last_mark_telemetry_ms) >= position.mark_telemetry_ms;
        let mark = emit_mark
            .then(|| {
                position.last_mark_telemetry_ms = now_ms;
                position.latest_mark.clone()
            })
            .flatten();
        (mark, action, None)
    }

    pub fn on_curve_state(
        &self,
        bonding_curve: &str,
        state: CurveState,
        slot: u64,
        now_ms: u64,
        source: &'static str,
    ) -> (
        Option<ExitMark>,
        Option<ExitAction>,
        Option<ExitRouteFallbackLine>,
    ) {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions
            .values_mut()
            .find(|position| position.bonding_curve == bonding_curve)
        else {
            return (None, None, None);
        };
        let action = position.mark_from_source(state, slot, now_ms, source);
        if position.phase == ExitPhase::Graduated {
            return (
                None,
                None,
                Some(ExitRouteFallbackLine {
                    kind: "rustExitRouteFallback",
                    mint: position.mint.clone(),
                    bonding_curve: position.bonding_curve.clone(),
                    reason: "pumpfun-curve-complete",
                    route: "pumpswap-canonical",
                }),
            );
        }
        let emit_mark = action.is_some()
            || now_ms.saturating_sub(position.last_mark_telemetry_ms) >= position.mark_telemetry_ms;
        let mark = emit_mark
            .then(|| {
                position.last_mark_telemetry_ms = now_ms;
                position.latest_mark.clone()
            })
            .flatten();
        (mark, action, None)
    }

    pub fn on_source_sell(
        &self,
        source_wallet: &str,
        mint: &str,
        source_signature: &str,
        _sold_tokens_raw: Option<u64>,
        now_ms: u64,
    ) -> Option<ExitAction> {
        let mut positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let Some(position) = positions.get_mut(mint) else {
            drop(positions);
            self.remember_source_sell(source_wallet, mint, source_signature, now_ms);
            return None;
        };
        if position.source_wallet != source_wallet {
            return None;
        }
        if !position
            .seen_source_signatures
            .insert(source_signature.to_string())
        {
            return None;
        }
        position.source_sell(now_ms)
    }

    fn remember_source_sell(
        &self,
        source_wallet: &str,
        mint: &str,
        source_signature: &str,
        now_ms: u64,
    ) {
        let mut cache = self
            .recent_source_sells
            .lock()
            .expect("exit watcher source cache mutex poisoned");
        cache.retain(|_, signals| {
            signals.retain(|signal| now_ms.saturating_sub(signal.observed_at_ms) <= 60_000);
            !signals.is_empty()
        });
        let signals = cache
            .entry(source_cache_key(source_wallet, mint))
            .or_default();
        if !signals
            .iter()
            .any(|signal| signal.signature == source_signature)
        {
            signals.push(RecentSourceSell {
                signature: source_signature.to_string(),
                observed_at_ms: now_ms,
            });
        }
    }

    pub fn bonding_curves(&self) -> Vec<String> {
        let mut curves = self
            .positions
            .lock()
            .expect("exit watcher mutex poisoned")
            .values()
            .filter(|position| !matches!(position.phase, ExitPhase::Closed | ExitPhase::Graduated))
            .map(|position| position.bonding_curve.clone())
            .collect::<Vec<_>>();
        curves.sort();
        curves.dedup();
        curves
    }

    pub fn bonding_curves_needing_poll(&self, stale_after_ms: u64, now_ms: u64) -> Vec<String> {
        let mut curves = self
            .positions
            .lock()
            .expect("exit watcher mutex poisoned")
            .values()
            .filter(|position| !matches!(position.phase, ExitPhase::Closed | ExitPhase::Graduated))
            .filter(|position| {
                position
                    .latest_mark
                    .as_ref()
                    .map(|mark| now_ms.saturating_sub(mark.observed_at_ms) >= stale_after_ms)
                    .unwrap_or(true)
            })
            .map(|position| position.bonding_curve.clone())
            .collect::<Vec<_>>();
        curves.sort();
        curves.dedup();
        curves
    }

    pub fn should_flatten_wallet_residual(&self, mint: &str) -> bool {
        self.positions
            .lock()
            .expect("exit watcher mutex poisoned")
            .get(mint)
            .map(|position| matches!(position.phase, ExitPhase::Closed | ExitPhase::Graduated))
            .unwrap_or(false)
    }

    pub fn active_mints(&self) -> Vec<String> {
        let mut mints = self
            .positions
            .lock()
            .expect("exit watcher mutex poisoned")
            .values()
            .filter(|position| !matches!(position.phase, ExitPhase::Closed | ExitPhase::Graduated))
            .map(|position| position.mint.clone())
            .collect::<Vec<_>>();
        mints.sort();
        mints.dedup();
        mints
    }

    pub fn session_counts(&self) -> ExitWatcherSessionCounts {
        let positions = self.positions.lock().expect("exit watcher mutex poisoned");
        let mut counts = ExitWatcherSessionCounts::default();
        for position in positions.values() {
            if matches!(position.phase, ExitPhase::Closed | ExitPhase::Graduated) {
                continue;
            }
            counts.open_positions += 1;
            if let Some(pending) = position.pending_exit.as_ref() {
                counts.pending_sells += 1;
                if pending.status_unknown.is_some() {
                    counts.status_unknown_sells += 1;
                }
            }
            if position.tp_6003_salvage.is_some() {
                counts.tp6003_salvage_positions += 1;
            }
        }
        counts
    }

    pub fn tracked_mints(&self) -> Vec<String> {
        let mut mints = self
            .positions
            .lock()
            .expect("exit watcher mutex poisoned")
            .values()
            .map(|position| position.mint.clone())
            .collect::<Vec<_>>();
        mints.sort();
        mints.dedup();
        mints
    }
}

fn source_cache_key(source_wallet: &str, mint: &str) -> String {
    format!("{source_wallet}:{mint}")
}

fn status_ok(id: String, mint: String, phase: ExitPhase) -> ExitStatusLine {
    status_with_action(id, mint, phase, false)
}

fn status_with_action(
    id: String,
    mint: String,
    phase: ExitPhase,
    action_pending: bool,
) -> ExitStatusLine {
    ExitStatusLine {
        kind: "rustExitWatchResult",
        id,
        ok: true,
        mint,
        phase: Some(phase),
        error: None,
        action_pending: Some(action_pending),
    }
}

fn status_error(id: String, mint: String, error: &str) -> ExitStatusLine {
    ExitStatusLine {
        kind: "rustExitWatchResult",
        id,
        ok: false,
        mint,
        phase: None,
        error: Some(error.to_string()),
        action_pending: Some(false),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn thresholds() -> ExitThresholds {
        ExitThresholds {
            stop_loss_pct: 30.0,
            stop_loss_grace_ms: 5_000,
            take_profit_pct: 40.0,
            take_profit_sell_pct: 75.0,
            early_source_window_ms: 30_000,
            early_source_max_pnl_pct: 20.0,
            source_partial_sell_pct: 50.0,
            runner_drawdown_pct: 25.0,
            max_hold_ms: 0,
            sell_slippage_bps: 9_000,
            sell_trade_fee_bps: DEFAULT_PUMP_FUN_SELL_TRADE_FEE_BPS,
            normal_take_profit_capture_slippage_bps: 9_000,
            estimated_execution_fee_lamports: 0,
            decision_execution_fee_lamports: None,
            tp_6003_salvage_min_realized_pnl_pct: None,
            tp_6003_salvage_authority_max_age_ms: 250,
            min_acceptable_take_profit_pnl_pct: None,
        }
    }

    fn registration(entry_ts_ms: u64) -> StartPumpfunExitWatchCommand {
        StartPumpfunExitWatchCommand {
            id: "register-1".to_string(),
            mint: "mint".to_string(),
            source_wallet: "source".to_string(),
            bonding_curve: "curve".to_string(),
            entry_ts_ms,
            tokens_held_raw: 1_000_000,
            safe_sell_tokens_raw: None,
            cost_basis_lamports: 100_000_000,
            buy_confirmed: true,
            exact_balance_proof: true,
            pre_proof_tp_only: false,
            mark_telemetry_ms: 1_000,
            thresholds: thresholds(),
            sell: PumpfunSellTemplate {
                sell_v2: false,
                accounts: vec![WatchAccountMeta {
                    pubkey: "account".to_string(),
                    is_signer: false,
                    is_writable: true,
                }],
                compute_unit_limit: 300_000,
                priority_fee_micro_lamports: 0,
                jito_tip_lamports: 0,
                helius_tip_lamports: 0,
                use_jito_direct: false,
                use_jito_fallback: false,
                sender_rpc_fallback_enabled: true,
                execution_multi_send_enabled: false,
                jito_send_mode: "transaction".to_string(),
                jito_transaction_urls: vec![],
                jito_bundle_urls: vec![],
                sender_endpoint_url: String::new(),
                rpc_fallback_url: "rpc".to_string(),
                jito_auth_uuid: None,
            },
            shadow: false,
        }
    }

    fn curve_for_pnl_with_fee_bps(target_pct: f64, fee_bps: u64) -> CurveState {
        let tokens = 1_000_000_u64;
        let cost = 100_000_000_f64;
        let token_reserves = 1_000_000_000_u64;
        let target_net = cost * (1.0 + target_pct / 100.0);
        let gross = target_net / (1.0 - fee_bps as f64 / 10_000.0);
        let sol_reserves =
            (gross * (token_reserves as f64 + tokens as f64) / tokens as f64).round() as u64;
        CurveState {
            virtual_token_reserves: token_reserves,
            virtual_sol_reserves: sol_reserves,
            complete: false,
        }
    }

    fn curve_for_pnl(target_pct: f64) -> CurveState {
        curve_for_pnl_with_fee_bps(target_pct, DEFAULT_PUMP_FUN_SELL_TRADE_FEE_BPS)
    }

    fn mark(position: &mut WatchedPosition, pnl: f64, now: u64) -> Option<ExitAction> {
        position.mark(curve_for_pnl(pnl), 1, now)
    }

    fn encoded_curve(state: CurveState) -> Vec<u8> {
        let mut data = vec![0_u8; 49];
        data[8..16].copy_from_slice(&state.virtual_token_reserves.to_le_bytes());
        data[16..24].copy_from_slice(&state.virtual_sol_reserves.to_le_bytes());
        data[48] = u8::from(state.complete);
        data
    }

    #[test]
    fn stop_loss_is_not_armed_during_grace() {
        let mut position = WatchedPosition::new(registration(1_000)).unwrap();
        assert!(mark(&mut position, -50.0, 5_999).is_none());
        assert_eq!(position.phase, ExitPhase::OpenGrace);
        let action = mark(&mut position, -50.0, 6_000).unwrap();
        assert_eq!(action.reason, ExitReason::HardStopLoss);
        assert_eq!(action.tokens_in_raw, 1_000_000);
    }

    #[test]
    fn hard_stop_full_close_uses_escape_min_out() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let action = mark(&mut position, -40.0, 6_000).unwrap();

        assert_eq!(action.reason, ExitReason::HardStopLoss);
        assert_eq!(action.tokens_in_raw, 1_000_000);
        assert!(action.expected_sol_out_lamports > 1);
        assert_eq!(action.min_sol_out_lamports, 1);
        assert_eq!(action.hard_stop_threshold_pnl_pct, Some(-30.0));
    }

    #[test]
    fn hard_stop_action_carries_trigger_source_and_pnl() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let action = position
            .mark_from_source(curve_for_pnl(-60.0), 7, 6_000, "reserve-observation-file")
            .unwrap();

        assert_eq!(action.reason, ExitReason::HardStopLoss);
        assert_eq!(action.trigger_mark_source, "reserve-observation-file");
        assert_eq!(action.trigger_mark_slot, 7);
        assert_eq!(action.trigger_mark_observed_at_ms, 6_000);
        assert!(action.trigger_mark_pnl_pct <= -59.0);
    }

    #[test]
    fn false_hard_stop_can_be_cancelled_without_failed_exit_cooldown() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let action = position
            .mark_from_source(curve_for_pnl(-60.0), 7, 6_000, "reserve-observation-file")
            .unwrap();
        assert_eq!(position.phase, ExitPhase::Exiting);

        position
            .cancel_pending_hard_stop_revalidation(
                action.sequence,
                Some((curve_for_pnl(-6.0), 8, 6_010, "yellowstone-cache")),
            )
            .unwrap();

        assert_eq!(position.phase, ExitPhase::Open);
        assert_eq!(position.next_exit_retry_after_ms, 0);
        assert!(position.pending_exit.is_none());
        let mark = position.latest_mark.as_ref().unwrap();
        assert_eq!(mark.source, "yellowstone-cache");
        assert!(mark.pnl_pct > -30.0);
        assert!(
            position
                .mark_from_source(curve_for_pnl(45.0), 9, 6_011, "yellowstone-cache")
                .is_some(),
            "a cancelled stale hard stop must immediately resume active watching"
        );
    }

    #[test]
    fn unavailable_authority_cancels_false_hard_stop_without_dead_cooldown() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let action = position
            .mark_from_source(curve_for_pnl(-50.0), 7, 6_000, "reserve-observation-file")
            .unwrap();

        position
            .cancel_pending_hard_stop_revalidation(action.sequence, None)
            .unwrap();

        assert_eq!(position.phase, ExitPhase::Open);
        assert_eq!(position.next_exit_retry_after_ms, 0);
        assert!(position.pending_exit.is_none());
    }

    #[test]
    fn genuine_authoritative_hard_stop_remains_authorized() {
        let mut cmd = registration(0);
        cmd.thresholds.stop_loss_pct = 20.0;
        let mut position = WatchedPosition::new(cmd).unwrap();
        let action = position
            .mark_from_source(curve_for_pnl(-25.0), 7, 6_000, "yellowstone-cache")
            .unwrap();

        assert_eq!(action.reason, ExitReason::HardStopLoss);
        assert_eq!(action.trigger_mark_source, "yellowstone-cache");
        assert_eq!(action.hard_stop_threshold_pnl_pct, Some(-20.0));
        assert!(position.pending_exit.is_some());
    }

    #[test]
    fn max_hold_full_close_uses_escape_min_out() {
        let mut cmd = registration(0);
        cmd.thresholds.max_hold_ms = 30_000;
        let mut position = WatchedPosition::new(cmd).unwrap();
        let action = mark(&mut position, -6.0, 30_000).unwrap();

        assert_eq!(action.reason, ExitReason::MaxHold);
        assert_eq!(action.tokens_in_raw, 1_000_000);
        assert!(action.expected_sol_out_lamports > 1);
        assert_eq!(action.min_sol_out_lamports, 1);
    }

    #[test]
    fn take_profit_keeps_configured_min_out_guard() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let action = mark(&mut position, 45.0, 6_000).unwrap();
        let expected_min = action
            .expected_sol_out_lamports
            .saturating_mul(10_000_u64.saturating_sub(thresholds().sell_slippage_bps))
            / 10_000;

        assert_eq!(action.reason, ExitReason::TakeProfit);
        assert_eq!(action.tokens_in_raw, 750_000);
        assert_eq!(action.min_sol_out_mode, "normal_take_profit_capture");
        assert_eq!(action.min_sol_out_lamports, expected_min);
        assert!(action.min_sol_out_lamports > 1);
    }

    #[test]
    fn initial_take_profit_does_not_use_emergency_salvage_floor() {
        let mut cmd = registration(0);
        cmd.thresholds.take_profit_pct = 20.0;
        cmd.thresholds.min_acceptable_take_profit_pnl_pct = Some(5.0);
        cmd.thresholds.tp_6003_salvage_min_realized_pnl_pct = Some(5.0);
        cmd.thresholds.normal_take_profit_capture_slippage_bps = 500;
        cmd.thresholds.decision_execution_fee_lamports = Some(1_000);
        let mut position = WatchedPosition::new(cmd).unwrap();
        let action = mark(&mut position, 30.0, 6_000).unwrap();
        let salvage_floor = min_sol_out_for_fee_adjusted_pnl(
            action.cost_basis_lamports,
            action.decision_execution_fee_lamports,
            5.0,
        )
        .unwrap();
        let normal_floor = action.expected_sol_out_lamports.saturating_mul(9_500) / 10_000;

        assert_eq!(action.reason, ExitReason::TakeProfit);
        assert_eq!(action.min_sol_out_mode, "normal_take_profit_capture");
        assert_eq!(action.min_acceptable_take_profit_pnl_pct, None);
        assert_eq!(action.min_acceptable_take_profit_sol_out_lamports, None);
        assert_eq!(action.min_sol_out_lamports, normal_floor);
        assert!(action.min_sol_out_lamports > salvage_floor);
    }

    #[test]
    fn source_sell_revalues_from_latest_curve_when_mark_was_not_emitted() {
        let mut position = WatchedPosition::new(registration(1_000)).unwrap();
        position.latest_curve = Some(curve_for_pnl(10.0));

        let action = position
            .source_sell(2_000)
            .expect("source sell must create an exit");

        assert_eq!(action.reason, ExitReason::EarlySourceSell);
        assert_eq!(action.tokens_in_raw, 1_000_000);
        assert!(position.latest_mark.is_some());
    }

    #[test]
    fn early_source_sell_under_twenty_percent_exits_all() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        assert!(mark(&mut position, 10.0, 10_000).is_none());
        let action = position.source_sell(10_000).unwrap();
        assert_eq!(action.reason, ExitReason::EarlySourceSell);
        assert_eq!(action.tokens_in_raw, 1_000_000);
    }

    #[test]
    fn losing_source_sell_at_any_age_exits_all() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        assert!(mark(&mut position, -1.0, 45_000).is_none());
        let action = position.source_sell(45_000).unwrap();
        assert_eq!(action.reason, ExitReason::LosingSourceSell);
        assert_eq!(action.tokens_in_raw, 1_000_000);
    }

    #[test]
    fn profitable_late_source_sell_under_twenty_sells_half() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        assert!(mark(&mut position, 10.0, 45_000).is_none());
        let action = position.source_sell(45_000).unwrap();
        assert_eq!(action.reason, ExitReason::SourcePartial);
        assert_eq!(action.tokens_in_raw, 500_000);
    }

    #[test]
    fn source_flat_has_highest_precedence() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        assert!(mark(&mut position, 30.0, 45_000).is_none());
        let action = position.source_flat(45_000).unwrap();
        assert_eq!(action.reason, ExitReason::SourceFlat);
        assert_eq!(action.tokens_in_raw, 1_000_000);
    }

    #[test]
    fn tp_sells_seventy_five_percent_and_enters_runner_after_ack() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let action = mark(&mut position, 45.0, 6_000).unwrap();
        assert_eq!(action.reason, ExitReason::TakeProfit);
        assert_eq!(action.tokens_in_raw, 750_000);
        position
            .ack(
                action.sequence,
                true,
                Some(action.expected_sol_out_lamports),
            )
            .unwrap();
        assert_eq!(position.phase, ExitPhase::Runner);
        assert_eq!(position.remaining_tokens_raw, 250_000);
        assert_eq!(position.remaining_cost_lamports, 25_000_000);
    }

    #[test]
    fn runner_exits_on_source_sell_regardless_of_profit() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let tp = mark(&mut position, 45.0, 6_000).unwrap();
        position
            .ack(tp.sequence, true, Some(tp.expected_sol_out_lamports))
            .unwrap();
        let action = position.source_sell(8_000).unwrap();
        assert_eq!(action.reason, ExitReason::RunnerSourceSell);
        assert_eq!(action.tokens_in_raw, 250_000);
    }

    #[test]
    fn source_sell_before_take_profit_closes_future_runner() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        assert!(mark(&mut position, 25.0, 45_000).is_none());
        assert!(position.source_sell(45_000).is_none());
        let tp = mark(&mut position, 45.0, 46_000).unwrap();
        assert_eq!(tp.reason, ExitReason::TakeProfit);
        let follow_up = position
            .ack(tp.sequence, true, Some(tp.expected_sol_out_lamports))
            .unwrap()
            .unwrap();
        assert_eq!(follow_up.reason, ExitReason::RunnerSourceSell);
        assert_eq!(follow_up.tokens_in_raw, 250_000);
    }

    #[test]
    fn runner_exits_on_twenty_five_percent_high_water_drawdown() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let tp = mark(&mut position, 45.0, 6_000).unwrap();
        position
            .ack(tp.sequence, true, Some(tp.expected_sol_out_lamports))
            .unwrap();
        assert!(mark(&mut position, 100.0, 7_000).is_none());
        let peak = position.peak_net_sol_out_lamports;
        let mut state = curve_for_pnl(45.0);
        // Reprice until executable runner proceeds are below 75% of its high-water mark.
        state.virtual_sol_reserves = (state.virtual_sol_reserves as f64 * 0.70) as u64;
        let action = position.mark(state, 2, 8_000).unwrap();
        assert!(peak > 0);
        assert_eq!(action.reason, ExitReason::RunnerDrawdown);
    }

    #[test]
    fn in_flight_gate_prevents_duplicate_actions_and_failure_allows_retry() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let first = mark(&mut position, -40.0, 6_000).unwrap();
        assert!(mark(&mut position, -50.0, 6_001).is_none());
        position.ack(first.sequence, false, None).unwrap();
        assert!(mark(&mut position, -50.0, 6_002).is_none());
        let retry = mark(&mut position, -50.0, 6_001 + FAILED_EXIT_RETRY_COOLDOWN_MS).unwrap();
        assert_eq!(retry.reason, ExitReason::HardStopLoss);
        assert_ne!(retry.sequence, first.sequence);
    }

    #[test]
    fn status_timeout_nonzero_balance_stays_unknown_and_blocks_hy4_duplicate_tp() {
        let mut cmd = registration(0);
        cmd.thresholds.take_profit_pct = 20.0;
        cmd.thresholds.take_profit_sell_pct = 100.0;
        let mut position = WatchedPosition::new(cmd).unwrap();
        let first = mark(&mut position, 30.0, 6_000).unwrap();
        assert_eq!(first.reason, ExitReason::TakeProfit);

        position
            .mark_sell_status_unknown(first.sequence, "first-sell".to_string(), 6_010, 8_000)
            .unwrap();
        assert_eq!(position.phase, ExitPhase::SellSentStatusUnknown);
        assert!(position.pending_exit.is_some());

        assert!(
            position
                .reconcile_wallet_balance(1_000_000, 100_000_000, 8_050)
                .is_none(),
            "one nonzero balance observation during status unknown must not reopen"
        );
        assert_eq!(position.phase, ExitPhase::SellSentStatusUnknown);
        assert!(position.pending_exit.is_some());

        assert!(
            mark(&mut position, 70.0, 8_100).is_none(),
            "normal TP becoming true again must not emit a second sell"
        );
        let suppressed = position
            .latest_mark
            .as_ref()
            .and_then(|mark| mark.sell_status_unknown.as_ref())
            .expect("status unknown mark should expose suppressed sell telemetry");
        assert_eq!(suppressed.decision, "SELL_SUPPRESSED_STATUS_UNKNOWN");
        assert_eq!(suppressed.signature, "first-sell");

        position
            .ack(first.sequence, true, Some(first.expected_sol_out_lamports))
            .unwrap();
        assert_eq!(position.phase, ExitPhase::Closed);
        assert!(position.pending_exit.is_none());
    }

    #[test]
    fn status_timeout_wallet_flat_closes_without_retry() {
        let registry = ExitWatcherRegistry::default();
        assert!(registry.register(registration(0)).ok);
        let (_, action, _) =
            registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(50.0)), 1, 6_000);
        let action = action.unwrap();
        let status = registry.mark_sell_status_unknown(
            "unknown".to_string(),
            "mint".to_string(),
            action.sequence,
            "first-sell".to_string(),
            6_010,
            8_000,
        );
        assert!(status.ok);
        assert_eq!(status.phase, Some(ExitPhase::SellSentStatusUnknown));

        let close = registry.reconcile_wallet_flat("flat".to_string(), "mint".to_string());
        assert!(close.ok);
        assert_eq!(close.phase, Some(ExitPhase::Closed));
        assert_eq!(close.action_pending, Some(false));
    }

    #[test]
    fn repeated_nonzero_balances_during_status_unknown_do_not_retry() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let first = mark(&mut position, 50.0, 6_000).unwrap();
        position
            .mark_sell_status_unknown(first.sequence, "first-sell".to_string(), 6_010, 8_000)
            .unwrap();

        for now in [8_010, 8_200, 9_000] {
            assert!(position
                .reconcile_wallet_balance(1_000_000, 100_000_000, now)
                .is_none());
            assert!(mark(&mut position, 80.0, now + 1).is_none());
            assert_eq!(position.phase, ExitPhase::SellSentStatusUnknown);
            assert_eq!(
                position.pending_exit.as_ref().unwrap().action.sequence,
                first.sequence
            );
        }
    }

    #[test]
    fn status_unknown_blocks_hard_stop_and_max_hold_duplicate_sells() {
        let mut cmd = registration(0);
        cmd.thresholds.take_profit_pct = 20.0;
        cmd.thresholds.take_profit_sell_pct = 100.0;
        cmd.thresholds.stop_loss_pct = 10.0;
        cmd.thresholds.max_hold_ms = 8_000;
        let mut position = WatchedPosition::new(cmd).unwrap();
        let first = mark(&mut position, 30.0, 6_000).unwrap();
        position
            .mark_sell_status_unknown(first.sequence, "first-sell".to_string(), 6_010, 6_050)
            .unwrap();

        assert!(mark(&mut position, -50.0, 6_100).is_none());
        assert_eq!(position.phase, ExitPhase::SellSentStatusUnknown);
        assert!(mark(&mut position, -5.0, 8_001).is_none());
        assert_eq!(position.phase, ExitPhase::SellSentStatusUnknown);
        assert_eq!(
            position.pending_exit.as_ref().unwrap().action.sequence,
            first.sequence
        );
    }

    #[test]
    fn definitive_failure_after_status_unknown_allows_existing_retry_rules() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let first = mark(&mut position, -40.0, 6_000).unwrap();
        position
            .mark_sell_status_unknown(first.sequence, "first-sell".to_string(), 6_010, 8_000)
            .unwrap();
        position.ack(first.sequence, false, None).unwrap();
        assert_eq!(position.phase, first.phase_before);
        assert!(position.pending_exit.is_none());

        assert!(mark(&mut position, -50.0, 8_001).is_none());
        let retry = mark(&mut position, -50.0, 8_000 + FAILED_EXIT_RETRY_COOLDOWN_MS).unwrap();
        assert_eq!(retry.reason, ExitReason::HardStopLoss);
        assert_ne!(retry.sequence, first.sequence);
    }

    #[test]
    fn later_definitive_6003_from_status_unknown_enters_v12_salvage_boundary() {
        let mut cmd = registration(0);
        cmd.thresholds.take_profit_pct = 20.0;
        cmd.thresholds.take_profit_sell_pct = 100.0;
        cmd.thresholds.tp_6003_salvage_min_realized_pnl_pct = Some(5.0);
        cmd.thresholds.normal_take_profit_capture_slippage_bps = 500;
        let mut position = WatchedPosition::new(cmd).unwrap();
        let first = mark(&mut position, 30.0, 6_000).unwrap();
        position
            .mark_sell_status_unknown(first.sequence, "first-sell".to_string(), 6_010, 8_000)
            .unwrap();
        position
            .arm_tp_6003_salvage(
                first.sequence,
                9_000,
                Some(22),
                Some("first-sell".to_string()),
                5.0,
            )
            .unwrap();
        assert_eq!(position.phase, ExitPhase::Tp6003SalvageArmed);
        assert!(position.pending_exit.is_none());

        assert!(
            position
                .mark_from_source(curve_for_pnl(30.0), 23, 8_999, "yellowstone-cache")
                .is_none(),
            "pre-definitive-failure quotes cannot authorize salvage"
        );
        let retry = position
            .mark_from_source(curve_for_pnl(30.0), 24, 9_001, "yellowstone-cache")
            .expect("fresh post-6003 Yellowstone mark should authorize salvage");
        assert_eq!(retry.reason, ExitReason::TakeProfit);
        assert_eq!(retry.min_sol_out_mode, "tp_6003_salvage_min_realized_pnl");
        assert_ne!(retry.sequence, first.sequence);
    }

    #[test]
    fn protected_tp_6003_retry_now_bypasses_failed_exit_cooldown_and_reprices() {
        let mut cmd = registration(0);
        cmd.thresholds.take_profit_pct = 20.0;
        cmd.thresholds.take_profit_sell_pct = 100.0;
        cmd.thresholds.sell_slippage_bps = 500;
        cmd.thresholds.normal_take_profit_capture_slippage_bps = 500;
        cmd.thresholds.decision_execution_fee_lamports = Some(1_000);
        let mut position = WatchedPosition::new(cmd).unwrap();
        let first = mark(&mut position, 30.0, 6_000).unwrap();
        assert_eq!(first.reason, ExitReason::TakeProfit);
        assert_eq!(first.min_sol_out_mode, "normal_take_profit_capture");

        let retry = position
            .retry_profit_6003_now(first.sequence, curve_for_pnl(6.0), 2, 6_010, 5.0)
            .unwrap()
            .unwrap();

        assert_eq!(position.next_exit_retry_after_ms, 0);
        assert_eq!(retry.reason, ExitReason::TakeProfit);
        assert_eq!(retry.sequence, first.sequence + 1);
        assert_eq!(retry.min_sol_out_mode, "tp_6003_salvage_min_realized_pnl");
        assert_eq!(retry.min_acceptable_take_profit_pnl_pct, Some(5.0));
        assert!(
            retry.min_sol_out_lamports < first.min_sol_out_lamports,
            "recovery must not reuse the stale original protected min"
        );
        assert!(retry.fee_adjusted_pnl_pct >= 5.0);
    }

    #[test]
    fn protected_tp_6003_retry_now_stays_bounded_by_profit_floor() {
        let mut cmd = registration(0);
        cmd.thresholds.take_profit_pct = 20.0;
        cmd.thresholds.take_profit_sell_pct = 100.0;
        let mut position = WatchedPosition::new(cmd).unwrap();
        let first = mark(&mut position, 30.0, 6_000).unwrap();

        let retry = position
            .retry_profit_6003_now(first.sequence, curve_for_pnl(2.0), 2, 6_010, 5.0)
            .unwrap();

        assert!(retry.is_none());
        assert_eq!(position.next_exit_retry_after_ms, 0);
        assert!(position.pending_exit.is_none());
        assert!(matches!(position.phase, ExitPhase::Tp6003SalvageArmed));
        assert!(position.tp_6003_salvage.is_some());
    }

    #[test]
    fn protected_tp_6003_salvage_floor_boundaries_are_strict() {
        for (retry_pnl, should_retry) in [
            (20.0, true),
            (10.0, true),
            (5.0, true),
            (4.99, false),
            (0.0, false),
            (-10.0, false),
        ] {
            let mut cmd = registration(0);
            cmd.thresholds.take_profit_pct = 20.0;
            cmd.thresholds.take_profit_sell_pct = 100.0;
            let mut position = WatchedPosition::new(cmd).unwrap();
            let first = mark(&mut position, 30.0, 6_000).unwrap();

            let retry = position
                .retry_profit_6003_now(first.sequence, curve_for_pnl(retry_pnl), 2, 6_010, 5.0)
                .unwrap();

            assert_eq!(
                retry.is_some(),
                should_retry,
                "retry_pnl={retry_pnl} should_retry={should_retry}"
            );
            if let Some(action) = retry {
                assert_eq!(action.min_sol_out_mode, "tp_6003_salvage_min_realized_pnl");
                assert!(action.fee_adjusted_pnl_pct >= 5.0);
            }
        }
    }

    fn tp6003_armed_position(
        salvage_floor_pct: f64,
        first_tp_pnl: f64,
        failure_at_ms: u64,
    ) -> (WatchedPosition, ExitAction) {
        let mut cmd = registration(0);
        cmd.thresholds.take_profit_pct = 20.0;
        cmd.thresholds.take_profit_sell_pct = 100.0;
        cmd.thresholds.stop_loss_pct = 20.0;
        cmd.thresholds.stop_loss_grace_ms = 0;
        cmd.thresholds.max_hold_ms = 120_000;
        cmd.thresholds.normal_take_profit_capture_slippage_bps = 500;
        cmd.thresholds.tp_6003_salvage_min_realized_pnl_pct = Some(salvage_floor_pct);
        let mut position = WatchedPosition::new(cmd).unwrap();
        let first = position
            .mark_from_source(curve_for_pnl(first_tp_pnl), 10, 6_000, "yellowstone-cache")
            .unwrap();
        position
            .arm_tp_6003_salvage(
                first.sequence,
                failure_at_ms,
                Some(11),
                Some("failed-tp-sell-signature".to_string()),
                salvage_floor_pct,
            )
            .unwrap();
        assert_eq!(position.phase, ExitPhase::Tp6003SalvageArmed);
        assert!(position.pending_exit.is_none());
        (position, first)
    }

    #[test]
    fn gp_k3_fixture_retries_first_qualifying_post_failure_salvage_quote() {
        let (mut position, first) = tp6003_armed_position(5.0, 22.0609, 6_200);
        let retry = position
            .mark_from_source(curve_for_pnl(13.33), 12, 6_585, "yellowstone-cache")
            .expect("GpK3 +13.33 post-failure Yellowstone quote should authorize salvage");

        assert_eq!(retry.reason, ExitReason::TakeProfit);
        assert_eq!(retry.sequence, first.sequence + 1);
        assert_eq!(retry.min_sol_out_mode, "tp_6003_salvage_min_realized_pnl");
        assert_eq!(retry.min_acceptable_take_profit_pnl_pct, Some(5.0));
        assert!(retry.fee_adjusted_pnl_pct >= 5.0);
        assert_eq!(position.phase, ExitPhase::Exiting);
    }

    #[test]
    fn eight_weu_successful_v1_replay_still_authorizes_later_fresh_quote() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let retry = position
            .mark_from_source(curve_for_pnl(20.1178), 12, 6_900, "yellowstone-cache")
            .expect("8wEU later fresh +20.1178 Yellowstone quote should still retry");

        assert_eq!(retry.reason, ExitReason::TakeProfit);
        assert_eq!(retry.min_sol_out_mode, "tp_6003_salvage_min_realized_pnl");
        assert!(retry.fee_adjusted_pnl_pct >= 20.0);
    }

    #[test]
    fn post_tp6003_later_fresh_fifteen_percent_retries_without_normal_tp() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let retry = position
            .mark_from_source(curve_for_pnl(15.0), 12, 7_000, "yellowstone-cache")
            .expect("fresh +15 salvage should retry");

        assert_eq!(retry.reason, ExitReason::TakeProfit);
        assert_eq!(retry.min_acceptable_take_profit_pnl_pct, Some(5.0));
        assert!(retry.fee_adjusted_pnl_pct >= 5.0);
    }

    #[test]
    fn post_tp6003_later_fresh_seven_percent_retries_without_normal_tp() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let retry = position
            .mark_from_source(curve_for_pnl(7.0), 12, 7_000, "yellowstone-cache")
            .expect("fresh +7 salvage should retry");

        assert_eq!(retry.reason, ExitReason::TakeProfit);
        assert!(retry.fee_adjusted_pnl_pct >= 5.0);
    }

    #[test]
    fn post_tp6003_four_percent_does_not_retry_but_stays_armed() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let retry = position.mark_from_source(curve_for_pnl(4.0), 12, 7_000, "yellowstone-cache");

        assert!(retry.is_none());
        assert_eq!(position.phase, ExitPhase::Tp6003SalvageArmed);
        assert!(position.pending_exit.is_none());
        assert!(position.tp_6003_salvage.is_some());
    }

    #[test]
    fn post_tp6003_four_then_nine_retries_on_later_qualifying_quote() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        assert!(position
            .mark_from_source(curve_for_pnl(4.0), 12, 7_000, "yellowstone-cache")
            .is_none());
        let retry = position
            .mark_from_source(curve_for_pnl(9.0), 13, 7_500, "yellowstone-cache")
            .expect("later +9 quote should retry");

        assert_eq!(retry.reason, ExitReason::TakeProfit);
        assert_eq!(retry.min_sol_out_mode, "tp_6003_salvage_min_realized_pnl");
    }

    #[test]
    fn pre_failure_quote_cannot_authorize_tp6003_salvage() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let retry = position.mark_from_source(curve_for_pnl(20.0), 9, 6_199, "yellowstone-cache");

        assert!(retry.is_none());
        assert_eq!(position.phase, ExitPhase::Tp6003SalvageArmed);
    }

    #[test]
    fn stale_post_failure_quote_cannot_authorize_tp6003_salvage() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        position.thresholds.tp_6003_salvage_authority_max_age_ms = 250;
        position.mark_without_evaluate_from_source(
            curve_for_pnl(15.0),
            12,
            6_300,
            "yellowstone-cache",
        );
        let retry = position.evaluate(6_551);

        assert!(retry.is_none());
        assert_eq!(position.phase, ExitPhase::Tp6003SalvageArmed);
        let salvage = position
            .tp_6003_salvage_mark("yellowstone-cache", 12, 6_300, 15.0)
            .unwrap();
        assert_eq!(salvage.decision, "RETRY_AUTHORIZED");
        assert_eq!(
            position
                .tp_6003_salvage_decision(
                    "yellowstone-cache",
                    6_300,
                    6_551,
                    15.0,
                    position.tp_6003_salvage.as_ref().unwrap()
                )
                .0,
            "QUOTE_STALE"
        );
    }

    #[test]
    fn non_yellowstone_cache_quote_cannot_authorize_tp6003_salvage() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let retry =
            position.mark_from_source(curve_for_pnl(15.0), 12, 7_000, "reserve-observation-file");

        assert!(retry.is_none());
        assert_eq!(position.phase, ExitPhase::Tp6003SalvageArmed);
    }

    #[test]
    fn tp6003_salvage_does_not_emit_duplicate_while_retry_pending() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let first_retry = position
            .mark_from_source(curve_for_pnl(15.0), 12, 7_000, "yellowstone-cache")
            .unwrap();
        let duplicate =
            position.mark_from_source(curve_for_pnl(18.0), 13, 7_100, "yellowstone-cache");

        assert_eq!(first_retry.reason, ExitReason::TakeProfit);
        assert!(duplicate.is_none());
        assert!(position.pending_exit.is_some());
    }

    #[test]
    fn successful_tp6003_salvage_sell_clears_salvage_state() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let retry = position
            .mark_from_source(curve_for_pnl(15.0), 12, 7_000, "yellowstone-cache")
            .unwrap();
        position
            .ack(retry.sequence, true, Some(retry.expected_sol_out_lamports))
            .unwrap();

        assert_eq!(position.phase, ExitPhase::Closed);
        assert!(position.tp_6003_salvage.is_none());
        assert_eq!(position.remaining_tokens_raw, 0);
    }

    #[test]
    fn hard_stop_overrides_armed_tp6003_salvage_and_clears_after_success() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let hard_stop = position
            .mark_from_source(curve_for_pnl(-20.5), 12, 7_000, "yellowstone-cache")
            .expect("hard stop must remain active while salvage armed");

        assert_eq!(hard_stop.reason, ExitReason::HardStopLoss);
        position
            .ack(
                hard_stop.sequence,
                true,
                Some(hard_stop.expected_sol_out_lamports),
            )
            .unwrap();
        assert!(position.tp_6003_salvage.is_none());
        assert_eq!(position.phase, ExitPhase::Closed);
    }

    #[test]
    fn max_hold_overrides_armed_tp6003_salvage_and_clears_after_success() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let max_hold = position
            .mark_from_source(curve_for_pnl(4.0), 12, 120_001, "yellowstone-cache")
            .expect("max hold must remain active while salvage armed");

        assert_eq!(max_hold.reason, ExitReason::MaxHold);
        position
            .ack(
                max_hold.sequence,
                true,
                Some(max_hold.expected_sol_out_lamports),
            )
            .unwrap();
        assert!(position.tp_6003_salvage.is_none());
        assert_eq!(position.phase, ExitPhase::Closed);
    }

    #[test]
    fn repeated_tp6003_rearms_salvage_with_new_failure_boundary() {
        let (mut position, _) = tp6003_armed_position(5.0, 22.0, 6_200);
        let retry = position
            .mark_from_source(curve_for_pnl(15.0), 12, 7_000, "yellowstone-cache")
            .unwrap();
        position
            .arm_tp_6003_salvage(
                retry.sequence,
                8_000,
                Some(14),
                Some("failed-salvage-retry-signature".to_string()),
                5.0,
            )
            .unwrap();

        assert!(position
            .mark_from_source(curve_for_pnl(15.0), 13, 7_999, "yellowstone-cache")
            .is_none());
        let third = position
            .mark_from_source(curve_for_pnl(9.0), 15, 8_001, "yellowstone-cache")
            .expect("post-second-failure quote should authorize next retry");
        assert_eq!(third.reason, ExitReason::TakeProfit);
        assert_eq!(third.min_sol_out_mode, "tp_6003_salvage_min_realized_pnl");
    }

    #[test]
    fn ordinary_take_profit_without_6003_remains_unchanged() {
        let mut cmd = registration(0);
        cmd.thresholds.take_profit_pct = 20.0;
        cmd.thresholds.take_profit_sell_pct = 100.0;
        cmd.thresholds.normal_take_profit_capture_slippage_bps = 500;
        cmd.thresholds.tp_6003_salvage_min_realized_pnl_pct = Some(5.0);
        let mut position = WatchedPosition::new(cmd).unwrap();
        let tp = position
            .mark_from_source(curve_for_pnl(22.0), 12, 7_000, "yellowstone-cache")
            .unwrap();

        assert_eq!(tp.reason, ExitReason::TakeProfit);
        assert_eq!(tp.min_sol_out_mode, "normal_take_profit_capture");
        assert_eq!(tp.min_acceptable_take_profit_pnl_pct, None);
        assert!(position.tp_6003_salvage.is_none());
    }

    #[test]
    fn normal_tp_capture_quality_differs_from_emergency_salvage_floor() {
        let execution_fee = 1_000_u64;
        for target in [20.0, 25.0, 30.0, 50.0] {
            let mut cmd = registration(0);
            cmd.thresholds.take_profit_pct = 20.0;
            cmd.thresholds.take_profit_sell_pct = 100.0;
            cmd.thresholds.normal_take_profit_capture_slippage_bps = 500;
            cmd.thresholds.decision_execution_fee_lamports = Some(execution_fee);
            let mut position = WatchedPosition::new(cmd).unwrap();
            let action = mark(&mut position, target, 6_000).unwrap();
            let salvage_min = min_sol_out_for_fee_adjusted_pnl(
                action.cost_basis_lamports,
                action.decision_execution_fee_lamports,
                5.0,
            )
            .unwrap();
            let normal_min = action.expected_sol_out_lamports * 9_500 / 10_000;
            let normal_min_pnl = fee_adjusted_pnl_pct(
                normal_min,
                action.cost_basis_lamports,
                action.decision_execution_fee_lamports,
            );
            let executable_pnl = fee_adjusted_pnl_pct(
                action.expected_sol_out_lamports,
                action.cost_basis_lamports,
                action.decision_execution_fee_lamports,
            );

            assert_eq!(action.min_sol_out_lamports, normal_min);
            assert_eq!(action.min_sol_out_mode, "normal_take_profit_capture");
            assert!(executable_pnl >= target - 0.02);
            assert!(
                normal_min > salvage_min,
                "target {target} normal min should exceed emergency salvage"
            );
            assert!(
                normal_min_pnl > 10.0,
                "target {target} normal floor should preserve more than salvage"
            );
        }
    }

    #[test]
    fn queued_source_sell_fires_after_a_mark_arrives() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        assert!(position.source_sell(1_000).is_none());
        let action = mark(&mut position, 5.0, 2_000).unwrap();
        assert_eq!(action.reason, ExitReason::EarlySourceSell);
    }

    #[test]
    fn source_sell_is_queued_until_buy_confirmation() {
        let mut cmd = registration(0);
        cmd.buy_confirmed = false;
        let mut position = WatchedPosition::new(cmd).unwrap();
        assert!(mark(&mut position, 5.0, 1_000).is_none());
        assert!(position.source_sell(1_100).is_none());
        position.buy_confirmed = true;
        let action = position.evaluate(1_200).unwrap();
        assert_eq!(action.reason, ExitReason::EarlySourceSell);
    }

    #[test]
    fn source_sell_seen_before_registration_is_replayed_after_activation() {
        let registry = ExitWatcherRegistry::default();
        assert!(registry
            .on_source_sell("source", "mint", "source-sell-1", Some(1_000), 1_100)
            .is_none());
        let mut cmd = registration(1_000);
        cmd.buy_confirmed = false;
        assert!(registry.register(cmd).ok);
        let state = curve_for_pnl(5.0);
        let (_, action, _) = registry.on_curve_account("curve", &encoded_curve(state), 1, 1_200);
        assert!(action.is_none());
        let (_, action) = registry.activate(
            ActivatePumpfunExitWatchCommand {
                id: "activate".to_string(),
                mint: "mint".to_string(),
            },
            1_300,
        );
        assert_eq!(action.unwrap().reason, ExitReason::EarlySourceSell);
    }

    #[test]
    fn actual_balance_update_clears_stale_quoted_mark_before_activation() {
        let registry = ExitWatcherRegistry::default();
        let mut cmd = registration(1_000);
        cmd.buy_confirmed = false;
        cmd.tokens_held_raw = 50_000;
        assert!(registry.register(cmd).ok);

        let losing_state = curve_for_pnl(-40.0);
        let (_, action, _) =
            registry.on_curve_account("curve", &encoded_curve(losing_state), 1, 1_100);
        assert!(action.is_none());

        let status = registry.update(UpdatePumpfunExitWatchCommand {
            id: "update".to_string(),
            mint: "mint".to_string(),
            tokens_held_raw: 100_000,
            cost_basis_lamports: 1_000_000,
            exact_proof: true,
            proof_source: Some("test".to_string()),
        });
        assert!(status.ok);

        let (_, action) = registry.activate(
            ActivatePumpfunExitWatchCommand {
                id: "activate".to_string(),
                mint: "mint".to_string(),
            },
            1_200,
        );
        assert!(
            !matches!(
                action.as_ref().map(|action| action.reason),
                Some(ExitReason::HardStopLoss)
            ),
            "activation must not fire a stale quoted-token hard-stop after actual balance update"
        );
    }

    #[test]
    fn failed_pre_exact_safe_tp_with_deferred_exact_balance_does_not_strand_max_hold() {
        let registry = ExitWatcherRegistry::default();
        let mut cmd = registration(1_000);
        cmd.buy_confirmed = true;
        cmd.exact_balance_proof = false;
        cmd.pre_proof_tp_only = true;
        cmd.tokens_held_raw = 600_000;
        cmd.safe_sell_tokens_raw = Some(600_000);
        cmd.thresholds.max_hold_ms = 2_000;
        cmd.thresholds.stop_loss_grace_ms = 0;
        assert!(registry.register(cmd).ok);

        let (_, tp, _) =
            registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(160.0)), 1, 1_100);
        let tp = tp.expect("pre-proof TP should fire on the guaranteed safe amount");
        assert_eq!(tp.reason, ExitReason::TakeProfit);
        assert!(!tp.exact_balance_proof);

        let (update_status, update_action) = registry.reconcile_wallet_balance(
            "deferred-exact".to_string(),
            "mint".to_string(),
            1_000_000,
            100_000_000,
            1_150,
        );
        assert!(update_status.ok);
        assert_eq!(update_status.action_pending, Some(false));
        assert!(update_action.is_none());

        let (ack_status, retry) = registry.ack(AckPumpfunExitSellCommand {
            id: "failed-tp".to_string(),
            mint: "mint".to_string(),
            sequence: tp.sequence,
            ok: false,
            actual_sol_out_lamports: None,
            error: Some("{\"InstructionError\":[2,{\"Custom\":6003}]}".to_string()),
        });
        assert!(ack_status.ok);
        assert_eq!(ack_status.action_pending, Some(false));
        assert!(retry.is_none());

        let (_, close, _) =
            registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(-5.0)), 2, 3_100);
        let close = close.expect("max hold must still close after a failed pre-proof TP");
        assert_eq!(close.reason, ExitReason::MaxHold);
        assert_eq!(
            close.tokens_in_raw, 1_000_000,
            "deferred exact balance should become the authoritative sell size"
        );
        assert!(close.exact_balance_proof);
    }

    #[test]
    fn route_template_failure_preserves_inventory_in_recoverable_state() {
        let registry = ExitWatcherRegistry::default();
        let mut cmd = registration(0);
        cmd.thresholds.max_hold_ms = 1_000;
        assert!(registry.register(cmd).ok);

        let (_, action, _) =
            registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(-5.0)), 1, 1_000);
        let action = action.expect("max-hold should create an exit action");
        assert_eq!(action.reason, ExitReason::MaxHold);

        let status = registry.fail_exit_recoverable(
            "route-fail".to_string(),
            "mint".to_string(),
            action.sequence,
        );
        assert!(status.ok);
        assert_eq!(status.phase, Some(ExitPhase::ExitFailedRecoverable));
        assert_eq!(status.action_pending, Some(false));
        assert!(registry.active_mints().contains(&"mint".to_string()));

        let (_, follow_up, _) =
            registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(-40.0)), 2, 2_000);
        assert!(
            follow_up.is_none(),
            "recoverable route failures must not spam the same invalid sell"
        );
    }

    #[test]
    fn wallet_reconcile_reopens_closed_watch_and_sells_actual_remainder() {
        let registry = ExitWatcherRegistry::default();
        let mut cmd = registration(1_000);
        cmd.tokens_held_raw = 50_000;
        assert!(registry.register(cmd).ok);
        let (_, first_action, _) =
            registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(-40.0)), 1, 6_100);
        let first_action = first_action.unwrap();
        assert_eq!(first_action.reason, ExitReason::HardStopLoss);
        assert_eq!(first_action.tokens_in_raw, 50_000);

        let (ack_status, follow_up) = registry.ack(AckPumpfunExitSellCommand {
            id: "ack".to_string(),
            mint: "mint".to_string(),
            sequence: first_action.sequence,
            ok: true,
            actual_sol_out_lamports: Some(first_action.expected_sol_out_lamports),
            error: None,
        });
        assert!(ack_status.ok);
        assert_eq!(ack_status.phase, Some(ExitPhase::Closed));
        assert!(follow_up.is_none());
        assert!(!registry.active_mints().contains(&"mint".to_string()));
        assert!(registry.tracked_mints().contains(&"mint".to_string()));

        let (status, remainder_action) = registry.reconcile_wallet_balance(
            "wallet-reconcile".to_string(),
            "mint".to_string(),
            12_345,
            1_000_000,
            1_200,
        );
        assert!(status.ok);
        let remainder_action = remainder_action.unwrap();
        assert_eq!(remainder_action.reason, ExitReason::SourceFlat);
        assert_eq!(remainder_action.tokens_in_raw, 12_345);
    }

    #[test]
    fn duplicate_source_signature_cannot_trigger_a_second_partial_exit() {
        let registry = ExitWatcherRegistry::default();
        assert!(registry.register(registration(0)).ok);
        let state = curve_for_pnl(10.0);
        let _ = registry.on_curve_account("curve", &encoded_curve(state), 1, 45_000);
        let first = registry
            .on_source_sell("source", "mint", "same-signature", Some(1_000), 45_000)
            .unwrap();
        assert_eq!(first.reason, ExitReason::SourcePartial);
        let _ = registry.ack(AckPumpfunExitSellCommand {
            id: "ack".to_string(),
            mint: "mint".to_string(),
            sequence: first.sequence,
            ok: true,
            actual_sol_out_lamports: Some(first.expected_sol_out_lamports),
            error: None,
        });
        assert!(registry
            .on_source_sell("source", "mint", "same-signature", Some(1_000), 45_100)
            .is_none());
    }

    #[test]
    fn duplicate_pre_registration_source_signal_is_replayed_once() {
        let registry = ExitWatcherRegistry::default();
        assert!(registry
            .on_source_sell("source", "mint", "same-signature", Some(6_000_000), 1_100)
            .is_none());
        assert!(registry
            .on_source_sell("source", "mint", "same-signature", Some(6_000_000), 1_101)
            .is_none());
        let mut cmd = registration(1_000);
        cmd.buy_confirmed = false;
        assert!(registry.register(cmd).ok);
        let _ = registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(5.0)), 1, 1_200);
        let (_, action) = registry.activate(
            ActivatePumpfunExitWatchCommand {
                id: "activate".to_string(),
                mint: "mint".to_string(),
            },
            1_300,
        );
        assert_eq!(action.unwrap().reason, ExitReason::EarlySourceSell);
    }

    #[test]
    fn source_flat_during_partial_exit_closes_remainder_after_ack() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        assert!(mark(&mut position, 10.0, 45_000).is_none());
        let partial = position.source_sell(45_000).unwrap();
        assert_eq!(partial.reason, ExitReason::SourcePartial);
        assert!(position.source_flat(45_001).is_none());
        let follow_up = position
            .ack(
                partial.sequence,
                true,
                Some(partial.expected_sol_out_lamports),
            )
            .unwrap()
            .unwrap();
        assert_eq!(follow_up.reason, ExitReason::SourceFlat);
        assert_eq!(follow_up.tokens_in_raw, 500_000);
    }

    #[test]
    fn command_bridge_queues_then_activates_source_exit() {
        let registry = ExitWatcherRegistry::default();
        let start = serde_json::json!({
            "kind": "startPumpfunExitWatch",
            "id": "start",
            "mint": "mint",
            "sourceWallet": "source",
            "bondingCurve": "curve",
            "entryTsMs": 1000,
            "tokensHeldRaw": 1000000,
            "costBasisLamports": 100000000,
            "buyConfirmed": false,
            "markTelemetryMs": 1000,
            "thresholds": thresholds(),
            "sell": registration(1000).sell,
            "shadow": true
        });
        let (status, action) =
            handle_watcher_command_line(&start.to_string(), &registry, 1_000).unwrap();
        assert!(status.ok);
        assert!(action.is_none());
        let _ = registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(5.0)), 1, 1_100);
        let flat =
            serde_json::json!({"kind":"sourceFlatPumpfunExitWatch","id":"flat","mint":"mint"});
        let (status, action) =
            handle_watcher_command_line(&flat.to_string(), &registry, 1_200).unwrap();
        assert!(status.ok);
        assert!(action.is_none());
        let activate =
            serde_json::json!({"kind":"activatePumpfunExitWatch","id":"activate","mint":"mint"});
        let (status, action) =
            handle_watcher_command_line(&activate.to_string(), &registry, 1_300).unwrap();
        assert!(status.ok);
        assert_eq!(action.unwrap().reason, ExitReason::SourceFlat);
    }

    #[test]
    fn detector_source_sell_bridge_applies_flat_atomically_and_dedupes() {
        let registry = ExitWatcherRegistry::default();
        assert!(registry.register(registration(0)).ok);
        let _ = registry.on_curve_account("curve", &encoded_curve(curve_for_pnl(10.0)), 1, 45_000);

        let command = serde_json::json!({
            "kind": "sourceSellPumpfunExitWatch",
            "id": "source-sell",
            "mint": "mint",
            "sourceWallet": "source",
            "sourceSignature": "detector-signature",
            "soldTokensRaw": 500000,
            "sourceFlat": true
        });
        let (status, action) =
            handle_watcher_command_line(&command.to_string(), &registry, 45_000).unwrap();
        assert!(status.ok);
        assert_eq!(action.unwrap().reason, ExitReason::SourceFlat);

        let duplicate = serde_json::json!({
            "kind": "sourceSellPumpfunExitWatch",
            "id": "source-sell-duplicate",
            "mint": "mint",
            "sourceWallet": "source",
            "sourceSignature": "detector-signature",
            "soldTokensRaw": 500000,
            "sourceFlat": true
        });
        let (status, action) =
            handle_watcher_command_line(&duplicate.to_string(), &registry, 45_001).unwrap();
        assert!(status.ok);
        assert!(action.is_none());

        let (_, follow_up) = registry.ack(AckPumpfunExitSellCommand {
            id: "ack".to_string(),
            mint: "mint".to_string(),
            sequence: 1,
            ok: true,
            actual_sol_out_lamports: None,
            error: None,
        });
        assert!(follow_up.is_none());
    }

    #[test]
    fn malformed_watcher_command_returns_correlated_error() {
        let registry = ExitWatcherRegistry::default();
        let line = r#"{"kind":"startPumpfunExitWatch","id":"bad","mint":"mint"}"#;
        let (status, action) = handle_watcher_command_line(line, &registry, 0).unwrap();
        assert!(!status.ok);
        assert_eq!(status.id, "bad");
        assert_eq!(status.mint, "mint");
        assert!(action.is_none());
    }

    #[test]
    fn curve_completion_disables_pumpfun_exit_path() {
        let mut position = WatchedPosition::new(registration(0)).unwrap();
        let complete = CurveState {
            virtual_token_reserves: 1,
            virtual_sol_reserves: 1,
            complete: true,
        };
        assert!(position.mark(complete, 1, 6_000).is_none());
        assert_eq!(position.phase, ExitPhase::Graduated);
    }

    #[test]
    fn quote_matches_constant_product_and_protocol_fee() {
        let state = CurveState {
            virtual_token_reserves: 1_000,
            virtual_sol_reserves: 2_000,
            complete: false,
        };
        assert_eq!(quote_sell_lamports(state, 1_000), Some(988));
        assert_eq!(
            quote_sell_lamports_with_fee_bps(state, 1_000, 100),
            Some(990)
        );
    }
}
