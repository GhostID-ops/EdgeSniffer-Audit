import { Keypair, PublicKey, LAMPORTS_PER_SOL } from "@solana/web3.js";
import bs58 from "bs58";
import {
  getPrivateKeyBase58,
  setSimulationMode,
  trackedWallets,
  simulationMode,
  simulationSolBalance,
  minBuyLamports,
  maxBuyLamports,
  pollIntervalMs,
  blockPumpfunMayhem,
  riskCheckIntervalMs,
  riskFastIntervalMs,
  riskFastWindowMs,
  riskNearTriggerIntervalMs,
  riskNormalIntervalMs,
  riskNearTriggerPct,
  riskDriftPctPerSec,
  riskControlsEnabled,
  pumpfunMayhemStrict,
  maxRiskQuotesPerCheck,
  readConnection,
  mirrorSells,
  buyOncePerToken,
  maxOpenPositions,
  showMarketCap,
  stopLossPct,
  takeProfitPct,
  minHoldBeforeStopLossMs,
  trailingSteps,
  trailingEnabled,
  pumpfunDirectEnabled,
  shyftGrpcEnabled,
} from "./config.js";
import { valueOnCurve, tryPumpFunDirectSell } from "./pumpfun/route.js";
import {
  getNewSwaps,
  subscribeWalletSwaps,
  type Unsubscribe,
} from "./watcher.js";
import { subscribeGrpcWalletSwaps, type GrpcUnsubscribe } from "./grpcWatcher.js";
import {
  mirrorSwap,
  NoPositionError,
  AlreadyBoughtError,
  ImpactTooHighError,
  SourceDriftError,
  type Side,
  type MirrorResult,
} from "./mirror.js";
import { getQuote, getSwap, isJupiterRateLimitError } from "./jupiter.js";
import type { DetectedSwap } from "./types.js";
import { warmConnection, createSenderTransactionFromSwapResponse, broadcastTransactionWithSender } from "./sender.js";
import { confirmSignature } from "./confirm.js";
import { getSolBalance, formatSol, getTokenBalance, getTokenBalanceFast } from "./balance.js";
import { telegram, type TradeNotification, type PnlNotification, formatPnlForTelegram } from "./telegram.js";
import "./telegramBot.js"; // Initialize Telegram bot handler
import { hasEnoughSOLForTrade, updateBalanceCache, getBalanceInfo, isBalanceCriticallyLow } from "./solReserve.js";
import {
  formatSolAmount,
  formatTokenAmount,
  formatMarketCap,
  getTokenMarketCap,
  shortMint,
  WRAPPED_SOL_MINT,
} from "./tokenRegistry.js";
import {
  recordBuy,
  recordSell,
  getPositions,
  getPosition,
  getTotals,
  resetPositions,
  type PositionSnapshot,
  type PnlTotals,
} from "./pnl.js";
import { logTradeAsync } from "./tradeLog.js";
import {
  shouldBlockNewBuys,
  recordTradeOutcome,
  resetCircuitBreaker,
} from "./circuitBreaker.js";
import { startFeeOracle, stopFeeOracle } from "./feeOracle.js";
import { startBlockhashCache, stopBlockhashCache } from "./blockhashCache.js";

const WARM_INTERVAL_MS = 30_000;
const BALANCE_LOG_INTERVAL_MS = 60_000;
const MAX_RECENT_SWAPS = 50;

type LegView = {
  mint: string;
  symbol: string;
  name: string;
  decimals: number;
  amountRaw: number;
  display: string;
};

export type MirrorRecord = {
  ts: number;
  sourceSignature: string;
  wallet: string;
  side: Side;
  input: LegView;
  output: LegView;
  isSimulated: boolean;
  mirrorSignature?: string;
  marketCapUsd?: number | null;
  marketCapCheckedAt?: number | null;
  error?: string;
};

export type BotState = {
  running: boolean;
  startedAt: number | null;
  ourWallet: string | null;
  trackedWallets: string[];
  simulationMode: boolean;
  minBuySol: number;
  maxBuySol: number;
  pollIntervalMs: number;
  riskControlsEnabled: boolean;
  riskCheckIntervalMs: number;
  minHoldBeforeStopLossMs: number;
  stopLossPct: number | null;
  takeProfitPct: number | null;
  lastBalanceLamports: number | null;
  lastBalanceSol: number | null;
  lastBalanceAt: number | null;
  recentSwaps: MirrorRecord[];
  positions: PositionSnapshot[];
  totals: PnlTotals;
};

export const recentSwaps: MirrorRecord[] = [];

/**
 * TTL-bounded signature dedupe — same .has/.add surface as Set, but evicts entries older than
 * SIGNATURE_TTL_MS so a long-running session doesn't accumulate millions of WS-event signatures.
 *
 * Sweep cadence: every SIGNATURE_SWEEP_EVERY_N adds, we drop entries past the TTL. Cheap.
 */
const SIGNATURE_TTL_MS = 4 * 60 * 60 * 1000; // 4h — well past any polling fallback's reach.
const SIGNATURE_SWEEP_EVERY_N = 256;
class BoundedSignatureSet {
  private readonly entries = new Map<string, number>();
  private addsSinceSweep = 0;
  has(sig: string): boolean {
    return this.entries.has(sig);
  }
  add(sig: string): void {
    this.entries.set(sig, Date.now());
    if (++this.addsSinceSweep >= SIGNATURE_SWEEP_EVERY_N) {
      this.addsSinceSweep = 0;
      this.sweep();
    }
  }
  clear(): void {
    this.entries.clear();
    this.addsSinceSweep = 0;
  }
  private sweep(): void {
    const cutoff = Date.now() - SIGNATURE_TTL_MS;
    for (const [sig, ts] of this.entries) {
      if (ts < cutoff) this.entries.delete(sig);
    }
  }
  get size(): number {
    return this.entries.size;
  }
}
const processedSignatures = new BoundedSignatureSet();
const buyMintsInFlight = new Set<string>();
const boughtMintsThisSession = new Set<string>();
const sourceBuySignatureByMint = new Map<string, string>();
const openPositionBuyMintsInFlight = new Set<string>();
const riskSellsInFlight = new Set<string>();
const riskSellRetryAfterByMint = new Map<string, number>();
const RISK_SELL_FAILURE_COOLDOWN_MS = 15_000;
type RiskTracePoint = {
  ts: number;
  valueLamports: number;
  costBasisLamports: number;
  drawdownPct: number;
  stopLossTrue: boolean;
};
const riskTraceByMint = new Map<string, RiskTracePoint[]>();

type RiskQuoteState = {
  /** Last Jupiter quote PnL %. */
  lastPnlPct: number;
  /** Wall-clock timestamp of that quote. */
  lastQuotedAt: number;
  /** Wall-clock timestamp of the last loop tick that visited this position (quote or skip). */
  lastTickedAt: number;
  /** Highest PnL% observed for this position. Drives trailing-stop stair-steps. */
  peakPnlPct: number;
};
const riskQuoteStateByMint = new Map<string, RiskQuoteState>();
let running = false;
let startedAt: number | null = null;
let stopRequested = false;
let sleepResolve: (() => void) | null = null;
let keypair: Keypair | null = null;
let lastBalanceLamports: number | null = null;
let lastBalanceAt: number | null = null;
let unsubscribeWs: Unsubscribe | null = null;
let unsubscribeGrpc: GrpcUnsubscribe | null = null;
let riskPositionCursor = 0;
let mirrorSellsEnabled = mirrorSells; // initialized from MIRROR_SELLS env, runtime-toggleable
let buyOncePerTokenEnabled = buyOncePerToken; // initialized from BUY_ONCE_PER_TOKEN env
let showMarketCapEnabled = showMarketCap; // initialized from SHOW_MARKET_CAP env

async function maybeGetMarketCap(mint: string): Promise<number | null> {
  if (!showMarketCapEnabled) return null;
  return getTokenMarketCap(mint);
}

export function isMirrorSellsEnabled(): boolean {
  return mirrorSellsEnabled;
}

export function setMirrorSellsEnabled(enabled: boolean): void {
  mirrorSellsEnabled = enabled;
  console.log(`🔁 Mirror sells: ${enabled ? "ON — bot will copy source sells" : "OFF — exit manually with 'e' or 'a'"}`);
}

export function toggleMirrorSells(): void {
  setMirrorSellsEnabled(!mirrorSellsEnabled);
}

export function isBuyOncePerTokenEnabled(): boolean {
  return buyOncePerTokenEnabled;
}

export function setBuyOncePerTokenEnabled(enabled: boolean): void {
  buyOncePerTokenEnabled = enabled;
  console.log(`🔁 Buy-once-per-token: ${enabled ? "ON — duplicate buys of the same mint are skipped" : "OFF — bot will buy the same token multiple times (pyramiding)"}`);
}

export function toggleBuyOncePerToken(): void {
  setBuyOncePerTokenEnabled(!buyOncePerTokenEnabled);
}

export function isShowMarketCapEnabled(): boolean {
  return showMarketCapEnabled;
}

export function setShowMarketCapEnabled(enabled: boolean): void {
  showMarketCapEnabled = enabled;
  console.log(`🔁 Show market cap: ${enabled ? "ON — DexScreener MC shown in logs (may be inaccurate for fresh pump.fun tokens)" : "OFF — MC line skipped, no API call"}`);
}

export function toggleShowMarketCap(): void {
  setShowMarketCapEnabled(!showMarketCapEnabled);
}

export type ModeChangeResult = {
  ok: boolean;
  changed: boolean;
  simulationMode: boolean;
  message: string;
};

export function setBotSimulationMode(enabled: boolean): ModeChangeResult {
  if (simulationMode === enabled) {
    return {
      ok: true,
      changed: false,
      simulationMode,
      message: `Already in ${enabled ? "SIMULATION" : "LIVE"} mode.`,
    };
  }

  const openPositions = getPositions().filter((p) => p.heldRaw > 0);
  if (openPositions.length > 0) {
    return {
      ok: false,
      changed: false,
      simulationMode,
      message:
        `Cannot switch to ${enabled ? "SIMULATION" : "LIVE"} mode while ` +
        `${openPositions.length} position(s) are open. Close positions first.`,
    };
  }

  setSimulationMode(enabled);
  console.log(`Mode changed to ${enabled ? "SIMULATION" : "LIVE"} from runtime controls.`);
  return {
    ok: true,
    changed: true,
    simulationMode,
    message: `Mode changed to ${enabled ? "SIMULATION" : "LIVE"}.`,
  };
}

type TradeLogExtras = {
  realizedPnlLamports?: number;
  realizedPnlPct?: number;
  exitReason?: string;
  priceImpactPct?: number | null;
};

function pushSwap(rec: MirrorRecord, extras?: TradeLogExtras): void {
  recentSwaps.unshift(rec);
  if (recentSwaps.length > MAX_RECENT_SWAPS) recentSwaps.pop();

  // Fire-and-forget JSONL persistence so we have a paper trail across sessions.
  logTradeAsync({
    ts: rec.ts,
    side: rec.side,
    mirrorSignature: rec.mirrorSignature,
    sourceSignature: rec.sourceSignature,
    sourceWallet: rec.wallet,
    inputMint: rec.input.mint,
    outputMint: rec.output.mint,
    inputAmountRaw: rec.input.amountRaw,
    outputAmountRaw: rec.output.amountRaw,
    inputSymbol: rec.input.symbol,
    outputSymbol: rec.output.symbol,
    priceImpactPct: extras?.priceImpactPct ?? null,
    marketCapUsd: rec.marketCapUsd ?? null,
    realizedPnlLamports: extras?.realizedPnlLamports ?? null,
    realizedPnlPct: extras?.realizedPnlPct ?? null,
    exitReason: extras?.exitReason ?? null,
    error: rec.error,
  });
}

function pushRiskTrace(
  position: PositionSnapshot,
  valueLamports: number,
  drawdownPct: number
): void {
  const points = riskTraceByMint.get(position.mint) ?? [];
  points.push({
    ts: Date.now(),
    valueLamports,
    costBasisLamports: position.costBasisRemainingLamports,
    drawdownPct,
    stopLossTrue: stopLossPct != null && drawdownPct <= -stopLossPct,
  });
  if (points.length > 200) points.shift();
  riskTraceByMint.set(position.mint, points);
}

function printRiskTrace(
  position: PositionSnapshot,
  sellValueLamports: number,
  finalPnlPct: number,
  reason: string
): void {
  const points = riskTraceByMint.get(position.mint) ?? [];
  const entryValue = position.costBasisRemainingLamports;
  const entryPrice =
    position.heldRaw > 0 ? entryValue / position.heldRaw : 0;
  const sellPrice =
    position.heldRaw > 0 ? sellValueLamports / position.heldRaw : 0;

  console.log("");
  console.log(`STOP-LOSS / TAKE-PROFIT TRACE $${position.symbol}`);
  console.log(`Reason: ${reason}`);
  console.log(`Entry value: ${formatSolAmount(entryValue)} SOL`);
  console.log(`Entry price/raw: ${entryPrice.toFixed(12)} lamports`);
  for (const point of points) {
    const price =
      position.heldRaw > 0 ? point.valueLamports / position.heldRaw : 0;
    console.log(
      `${new Date(point.ts).toISOString()} observed=${formatSolAmount(point.valueLamports)} SOL price/raw=${price.toFixed(12)} drawdown=${point.drawdownPct.toFixed(1)}% stopLoss=${point.stopLossTrue ? "true" : "false"}`
    );
  }
  console.log(`Simulated sell value: ${formatSolAmount(sellValueLamports)} SOL`);
  console.log(`Simulated sell price/raw: ${sellPrice.toFixed(12)} lamports`);
  console.log(`Final PnL: ${finalPnlPct >= 0 ? "+" : ""}${finalPnlPct.toFixed(1)}%`);
  console.log("");
}

function interruptibleSleep(ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const t = setTimeout(() => {
      sleepResolve = null;
      resolve();
    }, ms);
    sleepResolve = () => {
      clearTimeout(t);
      sleepResolve = null;
      resolve();
    };
  });
}

async function updateBalance(pubkey: PublicKey): Promise<void> {
  try {
    if (simulationMode && simulationSolBalance != null) {
      const lamports = Math.round(simulationSolBalance * LAMPORTS_PER_SOL);
      lastBalanceLamports = lamports;
      lastBalanceAt = Date.now();
      console.log("Balance:", formatSol(lamports), "(simulated)");
      return;
    }
    const { lamports } = await getSolBalance(pubkey);
    lastBalanceLamports = lamports;
    lastBalanceAt = Date.now();
    console.log("Balance:", formatSol(lamports));
  } catch (err) {
    console.warn("Failed to fetch balance:", err);
  }
}

function legDisplay(leg: {
  meta: { mint: string; symbol: string; decimals: number };
  amountRaw: number;
}): string {
  if (leg.meta.mint === WRAPPED_SOL_MINT) {
    return `${formatSolAmount(leg.amountRaw)} SOL`;
  }
  return `${formatTokenAmount(leg.amountRaw, leg.meta.decimals)} $${leg.meta.symbol}`;
}

function toLegView(leg: MirrorResult["input"]): LegView {
  return {
    mint: leg.meta.mint,
    symbol: leg.meta.symbol,
    name: leg.meta.name,
    decimals: leg.meta.decimals,
    amountRaw: leg.amountRaw,
    display: legDisplay(leg),
  };
}

function tokenSideMint(result: MirrorResult): string | null {
  if (result.side === "buy") return result.output.meta.mint;
  if (result.side === "sell") return result.input.meta.mint;
  return null;
}

function mirroredBuyAmountLamports(sourceAmountLamports: number): number {
  return Math.max(minBuyLamports, Math.min(maxBuyLamports, sourceAmountLamports));
}

function pnlArrow(lamports: number): string {
  if (lamports > 0) return "+";
  if (lamports < 0) return "−";
  return "";
}

export function formatPnl(lamports: number): string {
  const abs = Math.abs(lamports);
  return `${pnlArrow(lamports)}${formatSolAmount(abs)} SOL`;
}

/** SOL per token - simple price display */
function tokenPriceInSol(solLamports: number, tokensRaw: number, decimals: number): number {
  if (tokensRaw <= 0) return 0;
  const sol = solLamports / 1_000_000_000;
  const tokens = tokensRaw / Math.pow(10, decimals);
  if (tokens <= 0) return 0;
  return sol / tokens;
}

function formatPrice(sol: number): string {
  if (sol >= 1) return sol.toFixed(4);
  if (sol >= 0.01) return sol.toFixed(6);
  return sol.toFixed(9);
}

function logTrade(
  result: MirrorResult,
  swap: DetectedSwap,
  marketCapUsd: number | null,
  marketCapCheckedAt: number | null
): void {
  const simTag = result.isSimulated ? " Simulated" : "";
  const inputDisplay = legDisplay(result.input);
  const outputDisplay = legDisplay(result.output);
  const mcLine = marketCapUsd != null ? `📈 MC: ${formatMarketCap(marketCapUsd)}` : `📊 MC: Not available yet`;
  const mcDisplay = marketCapUsd != null ? formatMarketCap(marketCapUsd) : undefined;
  const walletTag = `${swap.wallet.slice(0, 4)}…${swap.wallet.slice(-4)}`;

  if (result.side === "buy") {
    const pos = recordBuy(result.output.meta, result.output.amountRaw, result.input.amountRaw);
    const tokenMint = result.output.meta.mint;
    boughtMintsThisSession.add(tokenMint);
    sourceBuySignatureByMint.set(tokenMint, swap.signature);
    const sourceSol = formatSolAmount(swap.amountRaw);
    const mirroredSol = formatSolAmount(result.input.amountRaw);
    const clamped = swap.amountRaw !== result.input.amountRaw;
    const entryPrice = tokenPriceInSol(
      result.input.amountRaw,
      result.output.amountRaw,
      result.output.meta.decimals
    );
    const impactStr = result.priceImpactPct
      ? ` · impact ${(parseFloat(result.priceImpactPct) * 100).toFixed(2)}%`
      : "";
    console.log(`✅ Buy${simTag} (${walletTag}): ${mirroredSol} SOL${clamped ? ` (capped from ${sourceSol} SOL)` : ""}`);
    console.log(`🪙 Tokens: ${outputDisplay}`);
    console.log(`💹 Entry: ${formatPrice(entryPrice)} SOL per $${pos.symbol}${impactStr}`);
    console.log(`🔗 CA: ${tokenMint}`);
    if (mcLine) console.log(mcLine);
    if (!result.isSimulated) console.log(`📝 Tx: ${result.signature}`);
    console.log(
      `📦 Position $${pos.symbol}: ${formatTokenAmount(pos.heldRaw, pos.decimals)} held, spent ${formatSolAmount(pos.totalSolSpentLamports)} SOL`
    );
    console.log("");

    // Send Telegram notification
    const notification: TradeNotification = {
      type: "buy",
      symbol: pos.symbol,
      amount: outputDisplay,
      solAmount: mirroredSol,
      marketCap: mcDisplay,
      marketCapCheckedAt: marketCapCheckedAt ?? undefined,
      isSimulated: result.isSimulated,
      wallet: walletTag,
      tokenAddress: tokenMint,
      txSignature: result.isSimulated ? undefined : result.signature,
    };
    void telegram.notifyTrade(notification);

    return;
  }

  if (result.side === "sell") {
    const pos = recordSell(result.input.meta, result.input.amountRaw, result.output.amountRaw);
    const tokenMint = result.input.meta.mint;
    const exitPrice = tokenPriceInSol(
      result.output.amountRaw,
      result.input.amountRaw,
      result.input.meta.decimals
    );
    const avgEntryLamportsPerRaw =
      pos.totalBoughtRaw > 0 ? pos.totalSolSpentLamports / pos.totalBoughtRaw : 0;
    const exitLamportsPerRaw =
      result.input.amountRaw > 0 ? result.output.amountRaw / result.input.amountRaw : 0;
    const pctChange =
      avgEntryLamportsPerRaw > 0
        ? (exitLamportsPerRaw / avgEntryLamportsPerRaw - 1) * 100
        : 0;
    const pctStr = `${pctChange >= 0 ? "+" : ""}${pctChange.toFixed(1)}%`;

    const sellImpactStr = result.priceImpactPct
      ? ` · impact ${(parseFloat(result.priceImpactPct) * 100).toFixed(2)}%`
      : "";
    console.log(`✅ Sell${simTag} (${walletTag}): ${inputDisplay}`);
    console.log(`💰 Received: ${outputDisplay}`);
    console.log(`💹 Exit: ${formatPrice(exitPrice)} SOL / 1M $${pos.symbol}  (${pctStr} vs avg entry)${sellImpactStr}`);
    console.log(`🔗 CA: ${tokenMint}`);
    if (mcLine) console.log(mcLine);
    if (!result.isSimulated) console.log(`📝 Tx: ${result.signature}`);
    console.log(
      `📊 Realized PnL on $${pos.symbol}: ${formatPnl(pos.realizedPnlLamports)} (held: ${formatTokenAmount(Math.max(pos.heldRaw, 0), pos.decimals)})`
    );
    console.log("");

    // Send Telegram notification
    const notification: TradeNotification = {
      type: "sell",
      symbol: pos.symbol,
      amount: inputDisplay,
      solAmount: outputDisplay,
      marketCap: mcDisplay,
      marketCapCheckedAt: marketCapCheckedAt ?? undefined,
      isSimulated: result.isSimulated,
      wallet: walletTag,
      tokenAddress: tokenMint,
      pnl: formatPnl(pos.realizedPnlLamports),
      txSignature: result.isSimulated ? undefined : result.signature,
    };
    void telegram.notifyTrade(notification);

    return;
  }

  console.log(`🔄 Swap${simTag} (${walletTag}): ${inputDisplay} → ${outputDisplay}`);
  if (!result.isSimulated) console.log(`📝 Tx: ${result.signature}`);
  console.log("");
}

async function processSwap(swap: DetectedSwap, kp: Keypair): Promise<void> {
  const ts = Date.now();
  const processStartedAt = Date.now();
  const elapsed = () => `${Date.now() - processStartedAt}ms`;
  let lockedBuyMint: string | null = null;
  let lockedOpenPositionMint: string | null = null;
  const releaseBuyLocks = () => {
    if (lockedBuyMint) {
      buyMintsInFlight.delete(lockedBuyMint);
      lockedBuyMint = null;
    }
    if (lockedOpenPositionMint) {
      openPositionBuyMintsInFlight.delete(lockedOpenPositionMint);
      lockedOpenPositionMint = null;
    }
  };
  console.log(
    `⚡ Processing ${swap.inputMint === WRAPPED_SOL_MINT ? "buy" : "sell"} from ${swap.wallet.slice(0, 4)}…${swap.wallet.slice(-4)} (${swap.signature.slice(0, 8)}…)`
  );

  // Skip mirroring source sells if mirror-sells is OFF. We still log it so user has a paper trail.
  if (!mirrorSellsEnabled && swap.outputMint === WRAPPED_SOL_MINT) {
    console.log(
      `ℹ️  Source ${swap.wallet.slice(0, 4)}…${swap.wallet.slice(-4)} sold ${swap.inputMint.slice(0, 6)}… — not mirroring (mirror-sells OFF). Press 'm' to enable, or 'e'/'a' to exit manually.`
    );
    return;
  }

  // Circuit breaker gate: only blocks BUYS, never sells. Existing positions can always exit.
  if (swap.inputMint === WRAPPED_SOL_MINT) {
    const blocked = shouldBlockNewBuys();
    if (blocked) {
      console.log(`⛔ Skipping buy: ${blocked}.`);
      return;
    }
  }

  // Skip duplicate buys of the same token if buy-once is ON. Position lookup is in-memory (no API cost).
  if (buyOncePerTokenEnabled && swap.inputMint === WRAPPED_SOL_MINT) {
    const mint = swap.outputMint;
    const existing = getPosition(mint);
    if (boughtMintsThisSession.has(mint) || (existing && existing.buysCount > 0)) {
      console.log(
        `Skipping buy: already bought ${mint.slice(0, 8)}... this session (buy-once ON).`
      );
      return;
    }
    if (buyMintsInFlight.has(mint)) {
      console.log(
        `Skipping buy: ${mint.slice(0, 8)}... buy already in flight (buy-once ON).`
      );
      return;
    }
    buyMintsInFlight.add(mint);
    lockedBuyMint = mint;
  }

  if (maxOpenPositions != null && swap.inputMint === WRAPPED_SOL_MINT) {
    const mint = swap.outputMint;
    const existing = getPosition(mint);
    const isNewOpenPosition = !(existing && existing.heldRaw > 0);

    if (isNewOpenPosition) {
      const openCount = getPositions().filter((p) => p.heldRaw > 0).length;
      const pendingCount = openPositionBuyMintsInFlight.size;
      if (openCount + pendingCount >= maxOpenPositions) {
        console.log(
          `Skipping buy: max open positions reached (${openCount}/${maxOpenPositions}, ${pendingCount} pending).`
        );
        releaseBuyLocks();
        return;
      }

      openPositionBuyMintsInFlight.add(mint);
      lockedOpenPositionMint = mint;
    }
  }

  if (blockPumpfunMayhem && swap.inputMint === WRAPPED_SOL_MINT) {
    const { isPumpfunMayhemToken } = await import("./pumpfunMayhem.js");
    const mayhem = await isPumpfunMayhemToken(swap.outputMint);
    if (mayhem === true) {
      console.log(`Skipping buy: ${swap.outputMint.slice(0, 8)}... is a pump.fun Mayhem Mode token.`);
      releaseBuyLocks();
      return;
    }
    if (mayhem == null && pumpfunMayhemStrict) {
      console.log(`Skipping buy: could not verify Mayhem Mode status for ${swap.outputMint.slice(0, 8)}... (strict mode ON).`);
      releaseBuyLocks();
      return;
    }
  }

  // Fast SOL reserve check - only for buys and only in LIVE mode (sells don't spend SOL)
  if (swap.inputMint === WRAPPED_SOL_MINT && !simulationMode) {
    const mirroredAmount = mirroredBuyAmountLamports(swap.amountRaw);
    // Update balance cache (fast, cached)
    const currentBalance = await updateBalanceCache(readConnection, kp.publicKey);
    
    // Check if we have enough SOL after reserving gas fees
    if (!hasEnoughSOLForTrade(mirroredAmount, currentBalance)) {
      const balanceInfo = getBalanceInfo(currentBalance);
      console.log(`⛽ Insufficient SOL (reserve check):`);
      console.log(`   Total: ${balanceInfo.total} SOL`);
      console.log(`   Available: ${balanceInfo.available} SOL`);
      console.log(`   Reserved: ${balanceInfo.reserved} SOL`);
      console.log(`   Required: ${(mirroredAmount / LAMPORTS_PER_SOL).toFixed(4)} SOL`);
      if (mirroredAmount !== swap.amountRaw) {
        console.log(`   Source trade: ${(swap.amountRaw / LAMPORTS_PER_SOL).toFixed(4)} SOL`);
      }
      console.log(`   ⚠️ Skipping trade to preserve gas fees`);
      
      // Send Telegram notification for reserve protection
      void telegram.notifyError(
        `⛽ Trade skipped: Insufficient SOL for gas fees`,
        `Available: ${balanceInfo.available} SOL | Required: ${(mirroredAmount / LAMPORTS_PER_SOL).toFixed(4)} SOL`
      );
      releaseBuyLocks();
      return;
    }
    
    // Warn if balance is getting low
    if (isBalanceCriticallyLow(currentBalance)) {
      const balanceInfo = getBalanceInfo(currentBalance);
      console.log(`⚠️ SOL balance critically low: ${balanceInfo.total} SOL (reserve: ${balanceInfo.reserved} SOL)`);
      void telegram.notifyError(
        `⚠️ SOL balance critically low`,
        `Total: ${balanceInfo.total} SOL | Available: ${balanceInfo.available} SOL`
      );
    }
  }
  
  // Log simulation mode notice for clarity
  if (simulationMode && swap.inputMint === WRAPPED_SOL_MINT) {
    console.log(`🎭 Simulation Mode: SOL reserve check disabled (no real gas fees)`);
  }
  
  try {
    const result = await mirrorSwap(swap, kp, {
      onFastBuyConfirmFailure: ({ mint }) => {
        if (boughtMintsThisSession.delete(mint)) {
          console.warn(
            `🧹 Buy-once marker cleared for ${mint.slice(0, 8)}… after failed fast-mode buy.`
          );
        }
      },
    });
    console.log(`⚡ Mirror path completed in ${elapsed()}`);
    const tokenMint = tokenSideMint(result);
    const marketCapUsd = tokenMint ? await maybeGetMarketCap(tokenMint) : null;
    const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;
    logTrade(result, swap, marketCapUsd, marketCapCheckedAt);
    const impactPct = result.priceImpactPct ? parseFloat(result.priceImpactPct) * 100 : null;
    // For mirrored sells we have realized PnL on the position now that recordSell ran inside logTrade.
    let realizedPnlLamports: number | undefined;
    let realizedPnlPct: number | undefined;
    if (result.side === "sell") {
      const pos = getPosition(result.input.meta.mint);
      if (pos) {
        realizedPnlLamports = pos.realizedPnlLamports;
        const cost = pos.totalSolSpentLamports;
        if (cost > 0) realizedPnlPct = (pos.realizedPnlLamports / cost) * 100;
      }
    }
    pushSwap(
      {
        ts,
        sourceSignature: swap.signature,
        wallet: swap.wallet,
        side: result.side,
        input: toLegView(result.input),
        output: toLegView(result.output),
        isSimulated: result.isSimulated,
        mirrorSignature: result.signature,
        marketCapUsd,
        marketCapCheckedAt,
      },
      { priceImpactPct: impactPct, realizedPnlLamports, realizedPnlPct }
    );
    if (result.side === "sell" && realizedPnlLamports != null) {
      // Feed the breaker only on actual closes — opens (buys) aren't outcomes yet.
      recordTradeOutcome(realizedPnlLamports);
    }
  } catch (err) {
    if (err instanceof NoPositionError) {
      console.log(
        `ℹ️ Skipping sell: no position held for ${err.mint.slice(0, 8)}…`
      );
      // Send Telegram notification for skipped sell
      void telegram.notifyError(
        `⚠️ Skipped sell: No position held for token ${err.mint.slice(0, 8)}…`,
        `Wallet: ${swap.wallet}`
      );
      return;
    }
    if (err instanceof AlreadyBoughtError) {
      console.log(
        `ℹ️ Skipping buy: already bought ${err.mint.slice(0, 8)}… this session`
      );
      return;
    }
    if (err instanceof ImpactTooHighError) {
      console.log(
        `🚫 Skipping buy: ${err.mint.slice(0, 8)}… impact ${err.impactPct.toFixed(1)}% > cap ${err.capPct.toFixed(1)}% even at MIN_BUY_SOL.`
      );
      void telegram.notifyError(
        `🚫 Buy skipped: impact too high`,
        `Token: ${err.mint.slice(0, 8)}… · impact ${err.impactPct.toFixed(1)}% > cap ${err.capPct.toFixed(1)}%`
      );
      return;
    }
    if (err instanceof SourceDriftError) {
      console.log(
        `🚫 Skipping buy: ${err.mint.slice(0, 8)}… source drift ${err.driftPct.toFixed(1)}% > cap ${err.capPct.toFixed(1)}% — chasing.`
      );
      void telegram.notifyError(
        `🚫 Buy skipped: chasing source`,
        `Token: ${err.mint.slice(0, 8)}… · drift ${err.driftPct.toFixed(1)}% > cap ${err.capPct.toFixed(1)}%`
      );
      return;
    }
    const msg = err instanceof Error ? err.message : String(err);
    
    // Check if this is a sell failure that shouldn't count as PnL
    const isSellFailure = swap.outputMint === WRAPPED_SOL_MINT && 
      (msg.includes("NOT_TRADABLE") || msg.includes("TOKEN_NOT_TRADABLE") || 
       msg.includes("No liquidity") || msg.includes("Insufficient liquidity"));
    
    if (isSellFailure) {
      console.log(`?? Sell failed for ${swap.inputMint.slice(0, 8)}...: ${msg}`);
      console.log(`?? This token appears untradable - PnL will not be affected`);
      
      // Send Telegram notification for failed sell
      void telegram.notifyError(
        `❌ Sell failed: Token ${swap.inputMint.slice(0, 8)}… is untradable`,
        `Error: ${msg}\nWallet: ${swap.wallet}`
      );
      
      // Record as a failed trade without affecting PnL
      pushSwap({
        ts,
        sourceSignature: swap.signature,
        wallet: swap.wallet,
        side: "sell",
        input: {
          mint: swap.inputMint,
          symbol: shortMint(swap.inputMint),
          name: shortMint(swap.inputMint),
          decimals: 0,
          amountRaw: swap.amountRaw,
          display: `${swap.amountRaw} raw`,
        },
        output: {
          mint: WRAPPED_SOL_MINT,
          symbol: "SOL",
          name: "Solana",
          decimals: 9,
          amountRaw: 0,
          display: "FAILED (untradable)",
        },
        isSimulated: simulationMode,
        error: `SELL FAILED: ${msg}`,
      });
      return;
    }
    
    console.error(`?? Mirror failed for ${swap.signature}: ${msg}`);
    
    // Send Telegram notification for general mirror errors
    const isSell = swap.inputMint !== WRAPPED_SOL_MINT;
    const action = isSell ? "sell" : "buy";
    const tokenMint = isSell ? swap.inputMint : swap.outputMint;
    void telegram.notifyError(
      `❌ ${action.charAt(0).toUpperCase() + action.slice(1)} failed: ${msg}`,
      `Token: ${tokenMint.slice(0, 8)}…\nWallet: ${swap.wallet}\nTx: ${swap.signature}`
    );
    
    pushSwap({
      ts,
      sourceSignature: swap.signature,
      wallet: swap.wallet,
      side:
        swap.inputMint === WRAPPED_SOL_MINT
          ? "buy"
          : swap.outputMint === WRAPPED_SOL_MINT
          ? "sell"
          : "swap",
      input: {
        mint: swap.inputMint,
        symbol: swap.inputMint === WRAPPED_SOL_MINT ? "SOL" : shortMint(swap.inputMint),
        name: swap.inputMint === WRAPPED_SOL_MINT ? "Solana" : shortMint(swap.inputMint),
        decimals: swap.inputMint === WRAPPED_SOL_MINT ? 9 : 0,
        amountRaw: swap.amountRaw,
        display: swap.inputMint === WRAPPED_SOL_MINT
          ? `${formatSolAmount(swap.amountRaw)} SOL`
          : `${swap.amountRaw} raw`,
      },
      output: {
        mint: swap.outputMint,
        symbol: swap.outputMint === WRAPPED_SOL_MINT ? "SOL" : shortMint(swap.outputMint),
        name: swap.outputMint === WRAPPED_SOL_MINT ? "Solana" : shortMint(swap.outputMint),
        decimals: swap.outputMint === WRAPPED_SOL_MINT ? 9 : 0,
        amountRaw: 0,
        display: "—",
      },
      isSimulated: simulationMode,
      error: msg,
    });
  } finally {
    releaseBuyLocks();
  }
}

async function loop(): Promise<void> {
  const kp = keypair!;
  const bootTimeSec = Math.floor((startedAt ?? Date.now()) / 1000);
  let lastWarm = 0;
  let lastBalanceLog = 0;
  let lastRiskCheck = 0;
  let lastPoll = 0;

  while (!stopRequested) {
    try {
      // Poll is a safety net — WebSocket does the fast path. Run serially so polls catch anything the WS missed.
      const loopStartedAt = Date.now();
      if (loopStartedAt - lastPoll >= pollIntervalMs) {
        const swaps = await getNewSwaps(trackedWallets, processedSignatures, bootTimeSec);
        for (const swap of swaps) {
          await processSwap(swap, kp);
        }
        lastPoll = Date.now();
      }

      const now = Date.now();
      if (now - lastWarm >= WARM_INTERVAL_MS) {
        await warmConnection();
        lastWarm = now;
      }
      if (now - lastBalanceLog >= BALANCE_LOG_INTERVAL_MS) {
        await updateBalance(kp.publicKey);
        lastBalanceLog = now;
      }
      // checkRiskExits now decides per-position cadence (fast / near-trigger / normal),
      // so we just call it on every loop tick and let it skip positions that aren't due.
      const minRiskInterval = Math.min(
        riskFastIntervalMs,
        riskNearTriggerIntervalMs,
        riskNormalIntervalMs
      );
      if (riskControlsEnabled && now - lastRiskCheck >= minRiskInterval) {
        await checkRiskExits();
        lastRiskCheck = now;
      }
    } catch (err) {
      console.error("Watcher error:", err);
    }

    if (stopRequested) break;
    // Loop floor must be small enough to honor the fastest tier; fall back to pollIntervalMs when risk is off.
    const sleepMs = riskControlsEnabled
      ? Math.min(
          pollIntervalMs,
          riskFastIntervalMs,
          riskNearTriggerIntervalMs,
          riskNormalIntervalMs
        )
      : pollIntervalMs;
    await interruptibleSleep(Math.max(100, sleepMs));
  }

  if (unsubscribeWs) {
    try {
      await unsubscribeWs();
    } catch {
      // best-effort
    }
    unsubscribeWs = null;
  }
  if (unsubscribeGrpc) {
    try {
      await unsubscribeGrpc();
    } catch {
      // best-effort
    }
    unsubscribeGrpc = null;
  }

  running = false;
  startedAt = null;
  stopRequested = false;
  stopFeeOracle();
  stopBlockhashCache();
  console.log("Bot stopped.");
}

export async function startBot(): Promise<void> {
  if (running) return;
  if (trackedWallets.length === 0) {
    throw new Error("No TRACKED_WALLETS set in .env.");
  }
  keypair = Keypair.fromSecretKey(bs58.decode(getPrivateKeyBase58()));
  running = true;
  startedAt = Date.now();
  stopRequested = false;
  resetCircuitBreaker();
  startFeeOracle();
  startBlockhashCache();
  console.log(
    "Copytrading bot started. Our wallet:",
    keypair.publicKey.toBase58()
  );
  console.log("Tracked wallets:", trackedWallets.length);
  
  // Send Telegram notification
  void telegram.notifyBotStatus("started");
  await updateBalance(keypair.publicKey);
  if (simulationMode) {
    console.log(
      "Running in SIMULATION mode –",
      trackedWallets.length,
      "wallet(s); no transactions will be sent."
    );
  }

  console.log("🧹 Fresh start — only trades from this moment onward will be mirrored.");

  if (riskControlsEnabled) {
    const trailDesc = trailingEnabled
      ? ` trailing=${trailingSteps.map((s) => `${s.peakPct}%→${s.floorPct >= 0 ? "+" : ""}${s.floorPct}%`).join(",")},`
      : "";
    console.log(
      `Risk exits enabled: SL ${stopLossPct ?? "off"}%, TP ${takeProfitPct ?? "off"}%,${trailDesc} ` +
        `tiers fast=${riskFastIntervalMs}ms (first ${riskFastWindowMs}ms) / near=${riskNearTriggerIntervalMs}ms (within ${riskNearTriggerPct}pp of trigger) / normal=${riskNormalIntervalMs}ms, ` +
        `drift=${riskDriftPctPerSec}pp/s, SL min hold ${minHoldBeforeStopLossMs}ms, max ${maxRiskQuotesPerCheck} quote(s)/tick.`
    );
  }
  if (maxOpenPositions != null) {
    console.log(`Max open positions enabled: ${maxOpenPositions}.`);
  }
  if (blockPumpfunMayhem) {
    console.log(`Pump.fun Mayhem Mode filter enabled${pumpfunMayhemStrict ? " (strict)" : ""}.`);
  }

  const kp = keypair;
  if (shyftGrpcEnabled) {
    unsubscribeGrpc = await subscribeGrpcWalletSwaps(
      trackedWallets,
      processedSignatures,
      (swap) => {
        processSwap(swap, kp).catch((err) => {
          console.error("gRPC processSwap error:", err);
        });
      }
    );
  } else {
    console.log("gRPC watcher disabled (set SHYFT_GRPC_ENABLED=true to enable).");
  }

  unsubscribeWs = await subscribeWalletSwaps(
    trackedWallets,
    processedSignatures,
    (swap) => {
      processSwap(swap, kp).catch((err) => {
        console.error("WS processSwap error:", err);
      });
    }
  );

  loop().catch((err) => {
    console.error("Loop crashed:", err);
    running = false;
    startedAt = null;
    stopRequested = false;
  });
}

export function stopBot(): void {
  if (!running) return;
  stopRequested = true;
  if (sleepResolve) sleepResolve();
  console.log("Stop requested...");
  
  // Send Telegram notification
  void telegram.notifyBotStatus("stopped");
}

/** Clear all positions, trade history, and processed-signature cache. */
export function resetTradingState(): void {
  resetPositions();
  recentSwaps.length = 0;
  processedSignatures.clear();
  buyMintsInFlight.clear();
  boughtMintsThisSession.clear();
  sourceBuySignatureByMint.clear();
  openPositionBuyMintsInFlight.clear();
  riskSellsInFlight.clear();
  riskSellRetryAfterByMint.clear();
  riskTraceByMint.clear();
  riskQuoteStateByMint.clear();
  resetCircuitBreaker();
  console.log("?? Cleared all positions, trade history, and signature cache.");
}

async function sellSimulatedPosition(
  position: PositionSnapshot,
  note?: string,
  quotedOutSolLamports?: number
): Promise<void> {
  const outSolLamports =
    quotedOutSolLamports ??
    parseInt(
      (await getQuote(position.mint, WRAPPED_SOL_MINT, position.heldRaw, 80))
        .outAmount ?? "0",
      10
    );
  if (!Number.isFinite(outSolLamports) || outSolLamports <= 0) {
    throw new Error(`No sell liquidity for $${position.symbol}`);
  }

  const meta = {
    mint: position.mint,
    symbol: position.symbol,
    name: position.symbol,
    decimals: position.decimals,
    isSol: false,
  };
  const snap = recordSell(meta, position.heldRaw, outSolLamports);
  const finalPnlPct =
    position.costBasisRemainingLamports > 0
      ? ((outSolLamports - position.costBasisRemainingLamports) /
          position.costBasisRemainingLamports) *
        100
      : 0;
  const marketCapUsd = await maybeGetMarketCap(position.mint);
  const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;

  console.log(`Sold (simulated): ${formatTokenAmount(position.heldRaw, position.decimals)} $${position.symbol}`);
  console.log(`Received: ${formatSolAmount(outSolLamports)} SOL`);
  console.log(`PnL: ${formatPnl(snap.realizedPnlLamports)}`);
  if (note) console.log(`Reason: ${note}`);
  if (note) printRiskTrace(position, outSolLamports, finalPnlPct, note);

  pushSwap(
    {
      ts: Date.now(),
      sourceSignature: `manual-sell-${position.mint}`,
      wallet: note ? "risk-sell" : "manual-sell",
      side: "sell",
      input: {
        mint: position.mint,
        symbol: position.symbol,
        name: position.symbol,
        decimals: position.decimals,
        amountRaw: position.heldRaw,
        display: `${formatTokenAmount(position.heldRaw, position.decimals)} $${position.symbol}`,
      },
      output: {
        mint: WRAPPED_SOL_MINT,
        symbol: "SOL",
        name: "Solana",
        decimals: 9,
        amountRaw: outSolLamports,
        display: `${formatSolAmount(outSolLamports)} SOL`,
      },
      isSimulated: true,
      marketCapUsd,
      marketCapCheckedAt,
    },
    {
      realizedPnlLamports: snap.realizedPnlLamports,
      realizedPnlPct: finalPnlPct,
      exitReason: note,
    }
  );
  recordTradeOutcome(snap.realizedPnlLamports);

  void telegram.notifyTrade({
    type: "sell",
    symbol: position.symbol,
    amount: formatTokenAmount(position.heldRaw, position.decimals),
    solAmount: formatSolAmount(outSolLamports),
    marketCap: marketCapUsd != null ? formatMarketCap(marketCapUsd) : undefined,
    marketCapCheckedAt: marketCapCheckedAt ?? undefined,
    isSimulated: true,
    wallet: note ? "risk" : "manual",
    tokenAddress: position.mint,
    pnl: formatPnl(snap.realizedPnlLamports),
    note,
    quotedPnlPct: note ? `${finalPnlPct >= 0 ? "+" : ""}${finalPnlPct.toFixed(1)}%` : undefined,
  });
}

/**
 * Sell specific position by index (#1, #2, etc)
 */
export async function sellPositionByIndex(index: number): Promise<void> {
  const open = getPositions().filter((p) => p.heldRaw > 0);
  
  if (open.length === 0) {
    console.log("?? No open positions to sell.");
    return;
  }
  
  if (index < 1 || index > open.length) {
    console.log(`?? Invalid position #${index}. Available positions: 1-${open.length}`);
    console.log("?? Use 'a' to sell all positions");
    return;
  }
  
  const position = open[index - 1]; // Convert to 0-based index
  if (simulationMode) {
    await sellSimulatedPosition(position);
    return;
  }

  if (!keypair) {
    throw new Error("Wallet is not loaded yet. Start the bot before selling positions.");
  }

  try {
    await sellLivePosition(position);
  } catch (error) {
    void telegram.notifyError(
      `Failed to sell position #${index} ($${position.symbol})`,
      error instanceof Error ? (error as Error).message : String(error)
    );
    throw error;
  }

  return;
}

/**
 * Sell all open positions (simulated or live based on mode)
 */
async function sellLivePosition(p: PositionSnapshot, note?: string): Promise<void> {
  let liveHeldRaw: number | null;
  try {
    // Fast path: direct ATA read. Falls back to scan-all on null (unusual token program / closed ATA).
    const fast = await getTokenBalanceFast(keypair!.publicKey, p.mint);
    liveHeldRaw = fast ?? (await getTokenBalance(keypair!.publicKey, p.mint));
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`Token balance lookup failed for $${p.symbol}: ${msg}; attempting tracked amount.`);
    liveHeldRaw = null;
  }
  // Chain authoritatively says we hold 0 (read succeeded, value was 0). Typical causes: transfer-fee /
  // honeypot mechanics drained the ATA, or an earlier sell landed but timed out before we recorded it.
  // Falling back to the tracked amount here is what was burning gas on every 15s risk-exit retry —
  // we'd broadcast a doomed swap that fails on-chain with Custom(6024) at the destination AMM.
  // Close the ghost position locally instead so the risk loop drops it from the candidate pool.
  if (liveHeldRaw === 0 && p.heldRaw > 0) {
    console.warn(
      `$${p.symbol}: wallet holds 0 on-chain but tracker shows ${formatTokenAmount(p.heldRaw, p.decimals)}. ` +
        `Closing position locally — no broadcast (likely transfer-fee/honeypot token or earlier unrecorded sell).`
    );
    recordSell(
      { mint: p.mint, symbol: p.symbol, name: p.symbol, decimals: p.decimals, isSol: false },
      p.heldRaw,
      0
    );
    return;
  }
  const sellRaw =
    liveHeldRaw != null && liveHeldRaw > 0
      ? Math.min(liveHeldRaw, p.heldRaw)
      : p.heldRaw;
  if (liveHeldRaw == null && p.heldRaw > 0) {
    console.warn(`Token balance lookup failed for $${p.symbol}; attempting tracked amount.`);
  }
  if (liveHeldRaw != null && liveHeldRaw > p.heldRaw) {
    console.warn(
      `Live balance for $${p.symbol} exceeds tracked position; selling tracked amount only. ` +
        `live=${formatTokenAmount(liveHeldRaw, p.decimals)}, tracked=${formatTokenAmount(p.heldRaw, p.decimals)}`
    );
  }
  if (sellRaw <= 0) {
    throw new Error(`No token balance available for $${p.symbol}.`);
  }

  const result = await mirrorSwap(
    {
      signature: sourceBuySignatureByMint.get(p.mint) ?? `manual-sell-${p.mint}`,
      wallet: "manual-sell",
      inputMint: p.mint,
      outputMint: WRAPPED_SOL_MINT,
      amountRaw: sellRaw,
      isNativeInput: false,
    },
    keypair!,
    { exactAmountRaw: sellRaw }
  );
  const soldRaw = result.input.amountRaw;
  const receivedLamports = result.output.amountRaw;
  const snap = recordSell(result.input.meta, soldRaw, receivedLamports);
  const marketCapUsd = await maybeGetMarketCap(p.mint);
  const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;
  const exitPrice = tokenPriceInSol(receivedLamports, soldRaw, p.decimals);
  const avgEntry = p.totalBoughtRaw > 0 ? p.totalSolSpentLamports / p.totalBoughtRaw : 0;
  const exitPerRaw = soldRaw > 0 ? receivedLamports / soldRaw : 0;
  const pct = avgEntry > 0 ? (exitPerRaw / avgEntry - 1) * 100 : 0;
  const quotedPnlPct =
    p.costBasisRemainingLamports > 0
      ? `${((receivedLamports - p.costBasisRemainingLamports) / p.costBasisRemainingLamports * 100) >= 0 ? "+" : ""}${(((receivedLamports - p.costBasisRemainingLamports) / p.costBasisRemainingLamports) * 100).toFixed(1)}%`
      : undefined;

  console.log(`Sell LIVE (manual): ${formatTokenAmount(soldRaw, p.decimals)} $${p.symbol}`);
  console.log(`Received: ${formatSolAmount(receivedLamports)} SOL`);
  console.log(`Exit: ${formatPrice(exitPrice)} SOL per $${p.symbol} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}% vs avg entry)`);
  console.log(`CA: ${p.mint}`);
  if (marketCapUsd != null) console.log(`MC: ${formatMarketCap(marketCapUsd)}`);
  console.log(`Tx: ${result.signature}`);
  console.log(`Realized PnL on $${p.symbol}: ${formatPnl(snap.realizedPnlLamports)}`);
  if (note) console.log(`Reason: ${note}`);
  console.log("");

  void telegram.notifyTrade({
    type: "sell",
    symbol: p.symbol,
    amount: formatTokenAmount(soldRaw, p.decimals),
    solAmount: formatSolAmount(receivedLamports),
    marketCap: marketCapUsd != null ? formatMarketCap(marketCapUsd) : undefined,
    marketCapCheckedAt: marketCapCheckedAt ?? undefined,
    isSimulated: false,
    wallet: "manual",
    tokenAddress: p.mint,
    pnl: formatPnl(snap.realizedPnlLamports),
    note,
    quotedPnlPct: note ? quotedPnlPct : undefined,
    txSignature: result.signature,
  });

  pushSwap(
    {
      ts: Date.now(),
      sourceSignature: `manual-sell-${p.mint}`,
      wallet: note ? "risk-sell" : "manual-sell",
      side: "sell",
      input: toLegView(result.input),
      output: toLegView(result.output),
      isSimulated: false,
      marketCapUsd,
      marketCapCheckedAt,
      mirrorSignature: result.signature,
    },
    {
      realizedPnlLamports: snap.realizedPnlLamports,
      realizedPnlPct: pct,
      exitReason: note,
      priceImpactPct: result.priceImpactPct ? parseFloat(result.priceImpactPct) * 100 : null,
    }
  );
  recordTradeOutcome(snap.realizedPnlLamports);
}

/**
 * Compute the effective stop threshold for a position given its peak PnL.
 * Starts at -stopLossPct (the base SL), then walks the trailing steps in ascending order;
 * each step the peak has cleared raises the threshold to its `floorPct`.
 * Returns the threshold as a signed % (e.g. -25 means "exit if pnl <= -25%", +5 means "+5% lock").
 */
function effectiveStopPct(peakPnlPct: number): number | null {
  if (stopLossPct == null) return null;
  let threshold = -stopLossPct;
  if (trailingEnabled) {
    for (const step of trailingSteps) {
      if (peakPnlPct >= step.peakPct) {
        threshold = Math.max(threshold, step.floorPct);
      } else {
        break; // steps are sorted asc; no later step can have triggered
      }
    }
  }
  return threshold;
}

function riskExitReason(
  pnlPct: number,
  positionAgeMs: number,
  peakPnlPct: number
): string | null {
  if (takeProfitPct != null && pnlPct >= takeProfitPct) {
    return `Take profit hit (${pnlPct.toFixed(1)}% >= ${takeProfitPct}%)`;
  }
  const threshold = effectiveStopPct(peakPnlPct);
  if (
    threshold != null &&
    pnlPct <= threshold &&
    positionAgeMs >= minHoldBeforeStopLossMs
  ) {
    if (trailingEnabled && threshold > -(stopLossPct ?? 0)) {
      return `Trailing stop hit (${pnlPct.toFixed(1)}% <= ${threshold >= 0 ? "+" : ""}${threshold.toFixed(1)}%, peak ${peakPnlPct.toFixed(1)}%)`;
    }
    return `Stop loss hit (${pnlPct.toFixed(1)}% <= ${threshold.toFixed(1)}%)`;
  }
  return null;
}

type RiskBucket = "fast" | "near" | "normal";

function bucketIntervalMs(bucket: RiskBucket): number {
  if (bucket === "fast") return riskFastIntervalMs;
  if (bucket === "near") return riskNearTriggerIntervalMs;
  return riskNormalIntervalMs;
}

/**
 * Decide whether we still need a Jupiter quote for this position right now, and which cadence bucket it belongs to.
 *
 * We can skip the quote when (lastPnl ± riskDriftPctPerSec × elapsed) cannot have crossed any trigger. This is the
 * "cheap price tracking" — no extra HTTP, just a worst-case projection from the last known quote.
 */
function planRiskTick(
  p: PositionSnapshot,
  state: RiskQuoteState | undefined,
  now: number
): { bucket: RiskBucket; shouldQuote: boolean; projectedPnlPct: number | null } {
  const ageMs = now - p.firstSeenAt;
  const inFastWindow = ageMs < riskFastWindowMs;

  // No prior quote → must quote.
  if (!state) {
    return {
      bucket: inFastWindow ? "fast" : "normal",
      shouldQuote: true,
      projectedPnlPct: null,
    };
  }

  const elapsedSec = Math.max(0, (now - state.lastQuotedAt) / 1000);
  const drift = riskDriftPctPerSec * elapsedSec;
  const lowerProjection = state.lastPnlPct - drift;
  const upperProjection = state.lastPnlPct + drift;

  // Effective stop level reflects trailing stair-steps that the peak has cleared.
  const effSL = effectiveStopPct(state.peakPnlPct);

  // Could a trigger have plausibly fired since the last quote?
  const couldHitSL =
    effSL != null && ageMs >= minHoldBeforeStopLossMs && lowerProjection <= effSL;
  const couldHitTP = takeProfitPct != null && upperProjection >= takeProfitPct;
  const triggerInRange = couldHitSL || couldHitTP;

  // Distance to nearest trigger using last known PnL.
  const distances: number[] = [];
  if (effSL != null) distances.push(Math.abs(state.lastPnlPct - effSL));
  if (takeProfitPct != null) distances.push(Math.abs(state.lastPnlPct - takeProfitPct));
  const nearestTriggerDist = distances.length ? Math.min(...distances) : Infinity;
  const nearTrigger = nearestTriggerDist <= riskNearTriggerPct;

  let bucket: RiskBucket;
  if (inFastWindow) bucket = "fast";
  else if (nearTrigger) bucket = "near";
  else bucket = "normal";

  const interval = bucketIntervalMs(bucket);
  const tickDue = now - state.lastTickedAt >= interval;

  // Always quote when projection says a trigger could already have fired, even if the bucket cadence hasn't hit.
  // Otherwise honor the cadence: quote only when the bucket interval has elapsed since the last tick.
  const shouldQuote = triggerInRange || tickDue;

  return { bucket, shouldQuote, projectedPnlPct: state.lastPnlPct };
}

async function checkRiskExits(): Promise<void> {
  if (!riskControlsEnabled) return;

  const open = getPositions().filter((p) => p.heldRaw > 0);
  if (open.length === 0) return;

  // First pass: pick the positions that are due to quote this tick.
  const now = Date.now();
  const candidates: { p: PositionSnapshot; bucket: RiskBucket }[] = [];
  for (let i = 0; i < open.length; i++) {
    const p = open[(riskPositionCursor + i) % open.length]!;
    if (riskSellsInFlight.has(p.mint)) continue;
    if ((riskSellRetryAfterByMint.get(p.mint) ?? 0) > now) continue;
    if (p.costBasisRemainingLamports <= 0) continue;
    const state = riskQuoteStateByMint.get(p.mint);
    const plan = planRiskTick(p, state, now);
    if (plan.shouldQuote) candidates.push({ p, bucket: plan.bucket });
  }
  if (candidates.length === 0) return;

  // Cap quotes per tick so risk checks can't starve buy quotes. Prioritize near-trigger > fast > normal.
  const priority: Record<RiskBucket, number> = { near: 0, fast: 1, normal: 2 };
  candidates.sort((a, b) => priority[a.bucket] - priority[b.bucket]);
  const positionsToCheck = candidates
    .slice(0, Math.min(maxRiskQuotesPerCheck, candidates.length))
    .map((c) => c.p);
  riskPositionCursor = (riskPositionCursor + 1) % open.length;

  for (const p of positionsToCheck) {
    if (riskSellsInFlight.has(p.mint)) continue;
    if ((riskSellRetryAfterByMint.get(p.mint) ?? 0) > Date.now()) continue;
    if (p.costBasisRemainingLamports <= 0) continue;

    try {
      // Curve-first valuation: pump.fun direct gives accurate, indexing-lag-free pricing.
      // Falls back to Jupiter only if the token isn't on a curve or has graduated.
      let outSolLamports: number;
      if (pumpfunDirectEnabled) {
        const onCurve = await valueOnCurve(p.mint, p.heldRaw);
        if (onCurve != null) {
          outSolLamports = onCurve.solOut;
        } else {
          const quote = await getQuote(p.mint, WRAPPED_SOL_MINT, p.heldRaw, 80);
          outSolLamports = parseInt(quote.outAmount ?? "0", 10);
        }
      } else {
        const quote = await getQuote(p.mint, WRAPPED_SOL_MINT, p.heldRaw, 80);
        outSolLamports = parseInt(quote.outAmount ?? "0", 10);
      }
      const tickAt = Date.now();
      if (!Number.isFinite(outSolLamports) || outSolLamports <= 0) {
        const prev = riskQuoteStateByMint.get(p.mint);
        riskQuoteStateByMint.set(p.mint, {
          lastPnlPct: prev?.lastPnlPct ?? 0,
          lastQuotedAt: prev?.lastQuotedAt ?? tickAt,
          lastTickedAt: tickAt,
          peakPnlPct: prev?.peakPnlPct ?? -Infinity,
        });
        continue;
      }

      const pnlPct =
        ((outSolLamports - p.costBasisRemainingLamports) /
          p.costBasisRemainingLamports) *
        100;
      const prevPeak = riskQuoteStateByMint.get(p.mint)?.peakPnlPct ?? -Infinity;
      const peakPnlPct = Math.max(prevPeak, pnlPct);
      riskQuoteStateByMint.set(p.mint, {
        lastPnlPct: pnlPct,
        lastQuotedAt: tickAt,
        lastTickedAt: tickAt,
        peakPnlPct,
      });
      pushRiskTrace(p, outSolLamports, pnlPct);
      const reason = riskExitReason(pnlPct, tickAt - p.firstSeenAt, peakPnlPct);
      if (!reason) continue;

      riskSellsInFlight.add(p.mint);
      console.log(`Risk exit triggered for $${p.symbol}: ${reason}`);
      console.log(
        `Quoted exit: ${formatSolAmount(outSolLamports)} SOL vs cost basis ${formatSolAmount(p.costBasisRemainingLamports)} SOL`
      );
      try {
        if (simulationMode) {
          await sellSimulatedPosition(p, reason, outSolLamports);
        } else {
          await sellLivePosition(p, reason);
        }
        riskSellRetryAfterByMint.delete(p.mint);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        riskSellRetryAfterByMint.set(p.mint, Date.now() + RISK_SELL_FAILURE_COOLDOWN_MS);
        void telegram.notifyRiskExit({
          status: "failed",
          symbol: p.symbol,
          reason,
          quotedSol: formatSolAmount(outSolLamports),
          costBasisSol: formatSolAmount(p.costBasisRemainingLamports),
          pnlPct: `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%`,
          error: msg,
          tokenAddress: p.mint,
          isSimulated: simulationMode,
        });
        throw err;
      } finally {
        riskSellsInFlight.delete(p.mint);
      }
    } catch (err) {
      riskSellsInFlight.delete(p.mint);
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(
        `Risk check skipped $${p.symbol}: ${msg}. Retrying after ${Math.round(RISK_SELL_FAILURE_COOLDOWN_MS / 1000)}s.`
      );
      if (isJupiterRateLimitError(err)) break;
    }
  }
}

export async function sellAllPositions(): Promise<void> {
  const open = getPositions().filter((p) => p.heldRaw > 0);
  if (open.length === 0) {
    console.log("?? No open positions to sell.");
    return;
  }

  if (simulationMode) {
    console.log(`?? Selling ${open.length} open position(s) at current market prices (SIMULATION)...`);
  } else {
    console.log(`?? Selling ${open.length} open position(s) at current market prices (LIVE)...`);
  }
  console.log("");

  if (!simulationMode) {
    for (const p of open) {
      try {
        await sellLivePosition(p);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`Failed to sell $${p.symbol}: ${msg}`);
      }
    }
    printPnlReport();
    return;
  }

  for (const p of open) {
    try {
      const quote = await getQuote(p.mint, WRAPPED_SOL_MINT, p.heldRaw, 80);
      const outSolLamports = parseInt(quote.outAmount ?? "0", 10);
      const sellImpactPctNum = quote.priceImpactPct ? parseFloat(quote.priceImpactPct) * 100 : null;
      const sellImpactStr = sellImpactPctNum != null ? ` · impact ${sellImpactPctNum.toFixed(2)}%` : "";
      if (!Number.isFinite(outSolLamports) || outSolLamports <= 0) {
        console.warn(`?? Skipped $${p.symbol}: no liquidity for quote.`);
        continue;
      }

      if (simulationMode) {
        // Simulate the sell
        const meta = {
          mint: p.mint,
          symbol: p.symbol,
          name: p.symbol,
          decimals: p.decimals,
          isSol: false,
        };
        const snap = recordSell(meta, p.heldRaw, outSolLamports);

        const marketCapUsd = await maybeGetMarketCap(p.mint);
        const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;
        const mcLine = marketCapUsd != null ? `?? MC: ${formatMarketCap(marketCapUsd)}` : `📊 MC: Not available yet`;

        const entryPriceA = tokenPriceInSol(p.totalSolSpentLamports, p.totalBoughtRaw, p.decimals);
        const exitPriceA = tokenPriceInSol(outSolLamports, p.heldRaw, p.decimals);
        const avgEntryA = p.totalBoughtRaw > 0 ? p.totalSolSpentLamports / p.totalBoughtRaw : 0;
        const exitPerRawA = p.heldRaw > 0 ? outSolLamports / p.heldRaw : 0;
        const pctA = avgEntryA > 0 ? (exitPerRawA / avgEntryA - 1) * 100 : 0;
        console.log(`?? Sell Simulated (manual): ${formatTokenAmount(p.heldRaw, p.decimals)} $${p.symbol}`);
        console.log(`?? Received: ${formatSolAmount(outSolLamports)} SOL`);
        console.log(`💹 Entry: ${formatPrice(entryPriceA)} SOL per $${p.symbol} → Exit: ${formatPrice(exitPriceA)} SOL per $${p.symbol} (${pctA >= 0 ? "+" : ""}${pctA.toFixed(1)}%)${sellImpactStr}`);
        console.log(`?? CA: ${p.mint}`);
        if (mcLine) console.log(mcLine);
        console.log(
          `?? Realized PnL on $${p.symbol}: ${formatPnl(snap.realizedPnlLamports)}`
        );
        console.log("");

        pushSwap({
          ts: Date.now(),
          sourceSignature: `manual-sell-${p.mint}`,
          wallet: "manual-sell",
          side: "sell",
          input: {
            mint: p.mint,
            symbol: p.symbol,
            name: p.symbol,
            decimals: p.decimals,
            amountRaw: p.heldRaw,
            display: `${formatTokenAmount(p.heldRaw, p.decimals)} $${p.symbol}`,
          },
          output: {
            mint: WRAPPED_SOL_MINT,
            symbol: "SOL",
            name: "Solana",
            decimals: 9,
            amountRaw: outSolLamports,
            display: `${formatSolAmount(outSolLamports)} SOL`,
          },
          isSimulated: true,
          marketCapUsd,
          marketCapCheckedAt,
        });
      } else {
        // Live sell - create and execute the transaction
        const swapResponse = await getSwap(quote, keypair!.publicKey.toBase58());
        const transaction = await createSenderTransactionFromSwapResponse(
          swapResponse as { swapTransaction: string; [key: string]: unknown },
          keypair!
        );
        
        const signature = await broadcastTransactionWithSender(transaction);
        await confirmSignature(signature);

        // Record the actual sell
        const meta = {
          mint: p.mint,
          symbol: p.symbol,
          name: p.symbol,
          decimals: p.decimals,
          isSol: false,
        };
        const snap = recordSell(meta, p.heldRaw, outSolLamports);

        const marketCapUsd = await maybeGetMarketCap(p.mint);
        const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;
        const mcLine = marketCapUsd != null ? `?? MC: ${formatMarketCap(marketCapUsd)}` : `📊 MC: Not available yet`;

        const exitPrice = tokenPriceInSol(
          outSolLamports,
          p.heldRaw,
          p.decimals
        );
        const avgEntry = p.totalBoughtRaw > 0 ? p.totalSolSpentLamports / p.totalBoughtRaw : 0;
        const exitPerRaw = p.heldRaw > 0 ? outSolLamports / p.heldRaw : 0;
        const pct = avgEntry > 0 ? (exitPerRaw / avgEntry - 1) * 100 : 0;
        console.log(`?? Sell LIVE (manual): ${formatTokenAmount(p.heldRaw, p.decimals)} $${p.symbol}`);
        console.log(`?? Received: ${formatSolAmount(outSolLamports)} SOL`);
        console.log(`💹 Exit: ${formatPrice(exitPrice)} SOL per $${p.symbol} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}% vs avg entry)${sellImpactStr}`);
        console.log(`?? CA: ${p.mint}`);
        if (mcLine) console.log(mcLine);
        console.log(`?? Tx: ${signature}`);
        console.log(
          `?? Realized PnL on $${p.symbol}: ${formatPnl(snap.realizedPnlLamports)}`
        );
        console.log("");

        pushSwap({
          ts: Date.now(),
          sourceSignature: `manual-sell-${p.mint}`,
          wallet: "manual-sell",
          side: "sell",
          input: {
            mint: p.mint,
            symbol: p.symbol,
            name: p.symbol,
            decimals: p.decimals,
            amountRaw: p.heldRaw,
            display: `${formatTokenAmount(p.heldRaw, p.decimals)} $${p.symbol}`,
          },
          output: {
            mint: WRAPPED_SOL_MINT,
            symbol: "SOL",
            name: "Solana",
            decimals: 9,
            amountRaw: outSolLamports,
            display: `${formatSolAmount(outSolLamports)} SOL`,
          },
          isSimulated: false,
          marketCapUsd,
          marketCapCheckedAt,
          mirrorSignature: signature,
        });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      
      // Check if this is an untradable token issue
      if (msg.includes("NOT_TRADABLE") || msg.includes("TOKEN_NOT_TRADABLE") || 
          msg.includes("No liquidity") || msg.includes("Insufficient liquidity")) {
        console.warn(`?? Token $${p.symbol} is untradable: ${msg}`);
        console.warn(`?? This position will remain held - PnL not affected`);
        
        // Don't record as a failed sell - just skip it
        continue;
      }
      
      console.warn(`?? Failed to sell $${p.symbol}: ${msg}`);
    }
  }

  printPnlReport();
}

/**
 * Simulated mark-to-market exit: quote each open position at current Jupiter prices,
 * record the sells into PnL, log them as simulated closes, then print the final report.
 * Only runs in simulation mode — live mode would need real on-chain sends.
 */
export async function closeAllOpenPositionsSimulated(): Promise<void> {
  if (!simulationMode) {
    console.log("?? Close-all only available in SIMULATION mode. Sell manually via the tracked wallet in live mode.");
    return;
  }

  const open = getPositions().filter((p) => p.heldRaw > 0);
  if (open.length === 0) {
    console.log("?? No open positions to close.");
    printPnlReport();
    return;
  }

  console.log(`🏁 Closing ${open.length} open position(s) at current market prices...`);
  console.log("");

  for (const p of open) {
    try {
      const quote = await getQuote(p.mint, WRAPPED_SOL_MINT, p.heldRaw, 80);
      const outSolLamports = parseInt(quote.outAmount ?? "0", 10);
      const sellImpactPctNum = quote.priceImpactPct ? parseFloat(quote.priceImpactPct) * 100 : null;
      const sellImpactStr = sellImpactPctNum != null ? ` · impact ${sellImpactPctNum.toFixed(2)}%` : "";
      if (!Number.isFinite(outSolLamports) || outSolLamports <= 0) {
        console.warn(`⚠️  Skipped $${p.symbol}: no liquidity for quote.`);
        continue;
      }

      const meta = {
        mint: p.mint,
        symbol: p.symbol,
        name: p.symbol,
        decimals: p.decimals,
        isSol: false,
      };

      const entryPrice = tokenPriceInSol(
        p.totalSolSpentLamports,
        p.totalBoughtRaw,
        p.decimals
      );
      const exitPrice = tokenPriceInSol(outSolLamports, p.heldRaw, p.decimals);
      const avgEntryLamportsPerRaw =
        p.totalBoughtRaw > 0 ? p.totalSolSpentLamports / p.totalBoughtRaw : 0;
      const exitLamportsPerRaw = p.heldRaw > 0 ? outSolLamports / p.heldRaw : 0;
      const pctChange =
        avgEntryLamportsPerRaw > 0
          ? (exitLamportsPerRaw / avgEntryLamportsPerRaw - 1) * 100
          : 0;
      const pctStr = `${pctChange >= 0 ? "+" : ""}${pctChange.toFixed(1)}%`;

      const snap = recordSell(meta, p.heldRaw, outSolLamports);

      const marketCapUsd = await maybeGetMarketCap(p.mint);
      const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;
      const mcLine = marketCapUsd != null ? `📈 MC: ${formatMarketCap(marketCapUsd)}` : `📊 MC: Not available yet`;

      console.log(`✅ Sell Simulated (manual close): ${formatTokenAmount(p.heldRaw, p.decimals)} $${p.symbol}`);
      console.log(`💰 Received: ${formatSolAmount(outSolLamports)} SOL`);
      console.log(`💹 Entry: ${formatPrice(entryPrice)} SOL / 1M  →  Exit: ${formatPrice(exitPrice)} SOL / 1M  (${pctStr})${sellImpactStr}`);
      console.log(`🔗 CA: ${p.mint}`);
      if (mcLine) console.log(mcLine);
      console.log(
        `📊 Realized PnL on $${p.symbol}: ${formatPnl(snap.realizedPnlLamports)}`
      );
      console.log("");

      pushSwap({
        ts: Date.now(),
        sourceSignature: `manual-close-${p.mint}`,
        wallet: "manual-close",
        side: "sell",
        input: {
          mint: p.mint,
          symbol: p.symbol,
          name: p.symbol,
          decimals: p.decimals,
          amountRaw: p.heldRaw,
          display: `${formatTokenAmount(p.heldRaw, p.decimals)} $${p.symbol}`,
        },
        output: {
          mint: WRAPPED_SOL_MINT,
          symbol: "SOL",
          name: "Solana",
          decimals: 9,
          amountRaw: outSolLamports,
          display: `${formatSolAmount(outSolLamports)} SOL`,
        },
        isSimulated: true,
        marketCapUsd,
        marketCapCheckedAt,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`⚠️  Failed to close $${p.symbol}: ${msg}`);
    }
  }

  printPnlReport();
}

export function isRunning(): boolean {
  return running;
}

export function printPnlReport(): void {
  const totals = getTotals();
  const positions = getPositions();
  const divider = "─".repeat(60);

  console.log("");
  console.log(divider);
  console.log("📊 SESSION PnL REPORT");
  console.log(divider);

  if (startedAt) {
    const elapsedMs = Date.now() - startedAt;
    const mins = Math.floor(elapsedMs / 60_000);
    const secs = Math.floor((elapsedMs % 60_000) / 1000);
    console.log(`⏱️  Session: ${mins}m ${secs}s`);
  }
  console.log(
    `📈 Trades: ${totals.tradesCount} (${totals.buysCount} buys, ${totals.sellsCount} sells)`
  );
  console.log(`🪙 Tokens tracked: ${totals.trackedTokens} (${totals.openPositions} open)`);
  console.log(`💸 SOL spent: ${formatSolAmount(totals.totalSolSpentLamports)} SOL`);
  console.log(`💰 SOL received: ${formatSolAmount(totals.totalSolReceivedLamports)} SOL`);
  console.log(`📊 Realized PnL: ${formatPnl(totals.realizedPnlLamports)}`);

  if (positions.length > 0) {
    const subdivider = "·".repeat(60);
    console.log("");
    console.log("🏷️  PER-TOKEN BREAKDOWN");
    console.log(subdivider);

    // Sort: open positions first, then by absolute PnL (biggest winners/losers surface).
    const sorted = [...positions].sort((a, b) => {
      const openDiff = (b.heldRaw > 0 ? 1 : 0) - (a.heldRaw > 0 ? 1 : 0);
      if (openDiff !== 0) return openDiff;
      return Math.abs(b.realizedPnlLamports) - Math.abs(a.realizedPnlLamports);
    });

    for (let i = 0; i < sorted.length; i++) {
      const p = sorted[i]!;
      const isOpen = p.heldRaw > 0;
      const pnlLamports = p.realizedPnlLamports;
      const pnlIcon = pnlLamports > 0 ? "🟢" : pnlLamports < 0 ? "🔴" : "⚪";
      const statusTag = isOpen
        ? `🟡 OPEN · ${formatTokenAmount(p.heldRaw, p.decimals)} held`
        : "⚫ CLOSED";

      const pctNum =
        p.totalSolSpentLamports > 0
          ? (pnlLamports / p.totalSolSpentLamports) * 100
          : 0;
      const pctStr =
        p.totalSolSpentLamports > 0
          ? ` (${pctNum >= 0 ? "+" : ""}${pctNum.toFixed(1)}%)`
          : "";

      console.log(`${pnlIcon} $${p.symbol}`);
      console.log(`   Status:    ${statusTag}`);
      console.log(`   PnL:       ${formatPnl(pnlLamports)}${pctStr}`);
      console.log(`   Spent:     ${formatSolAmount(p.totalSolSpentLamports)} SOL  (${p.buysCount} buy${p.buysCount === 1 ? "" : "s"})`);
      console.log(`   Received:  ${formatSolAmount(p.totalSolReceivedLamports)} SOL  (${p.sellsCount} sell${p.sellsCount === 1 ? "" : "s"})`);
      console.log(`   CA:        ${p.mint}`);
      if (i < sorted.length - 1) console.log("");
    }
  }
  console.log(divider);
  console.log("");

  // Send Telegram PnL report
  const pnlNotification: PnlNotification = {
    totalTrades: totals.tradesCount,
    totalBuys: totals.buysCount,
    totalSells: totals.sellsCount,
    realizedPnl: formatPnl(totals.realizedPnlLamports),
    unrealizedPnl: formatPnl(totals.totalSolReceivedLamports - totals.totalSolSpentLamports - totals.realizedPnlLamports),
    totalPnl: formatPnl(totals.totalSolReceivedLamports - totals.totalSolSpentLamports),
    winRate: "N/A",
    avgWin: "N/A",
    avgLoss: "N/A",
    openPositions: totals.openPositions,
  };
  void telegram.notifyPnlReport(pnlNotification);
}

export function getBotState(): BotState {
  return {
    running,
    startedAt,
    ourWallet: keypair?.publicKey.toBase58() ?? null,
    trackedWallets,
    simulationMode,
    minBuySol: minBuyLamports / LAMPORTS_PER_SOL,
    maxBuySol: maxBuyLamports / LAMPORTS_PER_SOL,
    pollIntervalMs,
    riskControlsEnabled,
    riskCheckIntervalMs,
    minHoldBeforeStopLossMs,
    stopLossPct,
    takeProfitPct,
    lastBalanceLamports,
    lastBalanceSol:
      lastBalanceLamports != null
        ? lastBalanceLamports / LAMPORTS_PER_SOL
        : null,
    lastBalanceAt,
    recentSwaps: recentSwaps.slice(),
    positions: getPositions(),
    totals: getTotals(),
  };
}


