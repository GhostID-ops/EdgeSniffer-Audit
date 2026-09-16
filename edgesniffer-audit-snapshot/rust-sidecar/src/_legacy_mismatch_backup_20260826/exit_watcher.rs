use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use serde::{Deserialize, Serialize};
use serde_json::Value;

const PUMP_FUN_TRADE_FEE_BPS: u64 = 100;
const FAILED_EXIT_RETRY_COOLDOWN_MS: u64 = 5_000;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum ExitPhase {
    OpenGrace,
    Open,
    Runner,
    Exiting,
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
    #[serde(default)]
    pub estimated_execution_fee_lamports: u64,
}

fn default_sell_slippage_bps() -> u64 {
    9_000
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
    pub cost_basis_lamports: u64,
    #[serde(default)]
    pub buy_confirmed: bool,
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
    let fee = gross.checked_mul(PUMP_FUN_TRADE_FEE_BPS as u128)? / 10_000;
    u64::try_from(gross.checked_sub(fee)?).ok()
}

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExitMark {
    pub kind: &'static str,
    pub mint: String,
    pub bonding_curve: String,
    pub phase: ExitPhase,
    pub slot: u64,
    pub observed_at_ms: u64,
    pub tokens_held_raw: u64,
    pub executable_sol_out_lamports: u64,
    pub net_sol_out_lamports: u64,
    pub remaining_cost_lamports: u64,
    pub pnl_pct: f64,
    pub peak_net_sol_out_lamports: u64,
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
    pub pnl_pct: f64,
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
}

#[derive(Debug, Clone)]
struct RecentSourceSell {
    signature: String,
    observed_at_ms: u64,
}

#[derive(Debug, Clone)]
struct WatchedPosition {
    mint: String,
    source_wallet: String,
    bonding_curve: String,
    entry_ts_ms: u64,
    remaining_tokens_raw: u64,
    remaining_cost_lamports: u64,
    buy_confirmed: bool,
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
    next_exit_retry_after_ms: u64,
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
            remaining_cost_lamports: cmd.cost_basis_lamports,
            buy_confirmed: cmd.buy_confirmed,
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
            next_exit_retry_after_ms: 0,
        })
    }

    fn mark(&mut self, state: CurveState, slot: u64, now_ms: u64) -> Option<ExitAction> {
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
        let executable = quote_sell_lamports(state, self.remaining_tokens_raw)?;
        let net = executable.saturating_sub(self.thresholds.estimated_execution_fee_lamports);
        let pnl_pct = pnl_pct(net, self.remaining_cost_lamports);
        self.peak_net_sol_out_lamports = self.peak_net_sol_out_lamports.max(net);
        self.latest_mark = Some(ExitMark {
            kind: "rustExitMark",
            mint: self.mint.clone(),
            bonding_curve: self.bonding_curve.clone(),
            phase: self.phase,
            slot,
            observed_at_ms: now_ms,
            tokens_held_raw: self.remaining_tokens_raw,
            executable_sol_out_lamports: executable,
            net_sol_out_lamports: net,
            remaining_cost_lamports: self.remaining_cost_lamports,
            pnl_pct,
            peak_net_sol_out_lamports: self.peak_net_sol_out_lamports,
        });
        self.evaluate(now_ms)
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
            || matches!(self.phase, ExitPhase::Closed | ExitPhase::Graduated)
        {
            return None;
        }
        if now_ms < self.next_exit_retry_after_ms {
            return None;
        }
        let mark = self.latest_mark.clone()?;
        let age_ms = now_ms.saturating_sub(self.entry_ts_ms);

        let decision = if self.source_flat {
            Some((ExitReason::SourceFlat, 100.0, ExitPhase::Closed))
        } else if self.source_sell_seen && self.phase == ExitPhase::Runner {
            Some((ExitReason::RunnerSourceSell, 100.0, ExitPhase::Closed))
        } else if self.pending_source_sell
            && age_ms < self.thresholds.early_source_window_ms
            && mark.pnl_pct < self.thresholds.early_source_max_pnl_pct
        {
            Some((ExitReason::EarlySourceSell, 100.0, ExitPhase::Closed))
        } else if self.pending_source_sell && mark.pnl_pct < 0.0 {
            Some((ExitReason::LosingSourceSell, 100.0, ExitPhase::Closed))
        } else if self.pending_source_sell
            && mark.pnl_pct >= 0.0
            && mark.pnl_pct < self.thresholds.early_source_max_pnl_pct
        {
            Some((
                ExitReason::SourcePartial,
                self.thresholds.source_partial_sell_pct,
                ExitPhase::Open,
            ))
        } else if !self.tp_taken && mark.pnl_pct >= self.thresholds.take_profit_pct {
            Some((
                ExitReason::TakeProfit,
                self.thresholds.take_profit_sell_pct,
                ExitPhase::Runner,
            ))
        } else if self.phase == ExitPhase::Runner
            && runner_drawdown_hit(
                mark.net_sol_out_lamports,
                self.peak_net_sol_out_lamports,
                self.thresholds.runner_drawdown_pct,
            )
        {
            Some((ExitReason::RunnerDrawdown, 100.0, ExitPhase::Closed))
        } else if self.thresholds.max_hold_ms > 0 && age_ms >= self.thresholds.max_hold_ms {
            Some((ExitReason::MaxHold, 100.0, ExitPhase::Closed))
        } else if age_ms >= self.thresholds.stop_loss_grace_ms
            && mark.pnl_pct <= -self.thresholds.stop_loss_pct
        {
            Some((ExitReason::HardStopLoss, 100.0, ExitPhase::Closed))
        } else {
            None
        };

        if self.pending_source_sell && decision.is_none() {
            self.pending_source_sell = false;
        }
        let (reason, sell_pct, phase_after_success) = decision?;
        let tokens = percent_of(self.remaining_tokens_raw, sell_pct);
        let state = self.latest_curve?;
        let expected = quote_sell_lamports(state, tokens)?;
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
        let min = if force_full_close {
            // Emergency exits are risk-off. A stale tight min-out can strand dust
            // positions while price keeps moving away from us.
            1
        } else {
            expected.saturating_mul(10_000_u64.saturating_sub(self.thresholds.sell_slippage_bps))
                / 10_000
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
            pnl_pct: mark.pnl_pct,
            shadow: self.shadow,
            sell: self.sell.clone(),
        };
        self.phase = ExitPhase::Exiting;
        self.pending_exit = Some(PendingExit {
            action: action.clone(),
        });
        Some(action)
    }

    fn ack(
        &mut self,
        sequence: u64,
        ok: bool,
        _actual_sol_out: Option<u64>,
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
            self.phase = pending.action.phase_before;
            self.next_exit_retry_after_ms = self
                .latest_mark
                .as_ref()
                .map(|mark| mark.observed_at_ms)
                .unwrap_or_default()
                .saturating_add(FAILED_EXIT_RETRY_COOLDOWN_MS);
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
        if pending.action.reason == ExitReason::TakeProfit {
            self.tp_taken = true;
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
            return None;
        }

        if matches!(self.phase, ExitPhase::Closed | ExitPhase::Graduated) {
            self.phase = ExitPhase::Open;
            self.buy_confirmed = true;
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

fn percent_of(value: u64, pct: f64) -> u64 {
    if pct >= 100.0 {
        return value;
    }
    (((value as f64 * pct / 100.0).floor() as u64).max(1)).min(value)
}

fn runner_drawdown_hit(current: u64, peak: u64, drawdown_pct: f64) -> bool {
    peak > 0 && (current as f64 / peak as f64 - 1.0) * 100.0 <= -drawdown_pct
}

#[derive(Debug, Clone, Default)]
pub struct ExitWatcherRegistry {
    positions: Arc<Mutex<HashMap<String, WatchedPosition>>>,
    recent_source_sells: Arc<Mutex<HashMap<String, Vec<RecentSourceSell>>>>,
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
        let action = position.mark(state, slot, now_ms);
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
            estimated_execution_fee_lamports: 0,
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
            cost_basis_lamports: 100_000_000,
            buy_confirmed: true,
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

    fn curve_for_pnl(target_pct: f64) -> CurveState {
        let tokens = 1_000_000_u64;
        let cost = 100_000_000_f64;
        let token_reserves = 1_000_000_000_u64;
        let target_net = cost * (1.0 + target_pct / 100.0);
        let gross = target_net / 0.99;
        let sol_reserves =
            (gross * (token_reserves as f64 + tokens as f64) / tokens as f64).round() as u64;
        CurveState {
            virtual_token_reserves: token_reserves,
            virtual_sol_reserves: sol_reserves,
            complete: false,
        }
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
        assert_eq!(action.min_sol_out_lamports, expected_min);
        assert!(action.min_sol_out_lamports > 1);
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
        assert_eq!(quote_sell_lamports(state, 1_000), Some(990));
    }
}
