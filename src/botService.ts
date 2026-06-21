import { Keypair, PublicKey, LAMPORTS_PER_SOL, type VersionedTransactionResponse } from "@solana/web3.js";
import { closeSync, mkdirSync, openSync, unlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import bs58 from "bs58";
import {
  getPrivateKeyBase58,
  setSimulationMode,
  trackedWallets,
  exitSignalSellsEnabled,
  exitSignalWallets,
  simulationMode,
  simulationSolBalance,
  minBuyLamports,
  maxBuyLamports,
  solReserveLamports,
  computeUnitLimit,
  useJitoDirect,
  useJitoFallback,
  HELIUS_TIP_MIN_LAMPORTS,
  minBuyUsdcRaw,
  maxBuyUsdcRaw,
  tipLamports,
  priorityFeeMicroLamports,
  mirrorSlippageBps,
  mirrorQuoteAsset,
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
  mirrorSellMode,
  mirrorSellProportionalMinPct,
  mirrorSellProportionalMaxPct,
  mirrorSellProportionalFallback,
  buyOncePerToken,
  skipSourcePreexistingMints,
  sourcePreexistingScanTxLimit,
  failedBuyRecovery,
  maxOpenPositions,
  maxBuysPerToken,
  maxSolPerTokenLamports,
  maxSessionDeployedLamports,
  maxOpenDeployedLamports,
  showMarketCap,
  stopLossPct,
  takeProfitPct,
  hybridExitEnabled,
  hybridExitTpPct,
  hybridExitTpSellPct,
  hybridExitSourceExitRemaining,
  sourceHoldExitEnabled,
  sourceHoldExitIntervalMs,
  sourceHoldExitConsecutiveMisses,
  sourceHoldExitMinPositionAgeMs,
  minHoldBeforeStopLossMs,
  trailingSteps,
  trailingEnabled,
  type TrailingStep,
  clusterTriggerEnabled,
  clusterWallets,
  clusterConfirmWallet,
  clusterMinSourceBuyLamports,
  clusterConfirmWindowMs,
  clusterNoConfirmTrailingSteps,
  pumpfunDirectEnabled,
  shyftGrpcEnabled,
  shyftRabbitStreamEnabled,
  rustSidecarEnabled,
  rustExecutorEnabled,
  rustOnlyBuys,
  rustHotSniperEnabled,
  rustHotSniperPrimary,
  rustHotSniperPrimaryFallbackEnabled,
  rustHotSniperPrimaryFallbackDelayMs,
  heliusTxSubscribeEnabled,
  confirmationNullStatusTimeoutMs,
  prebuyMoralisFilterEnabled,
  prebuyInferredMcFilterEnabled,
  copyBuyPercentage,
  minBuyBehavior,
  executionRescueRetries,
  executionRescueDelayMs,
  pnlAccountingBasis,
  useWalletPnlAccounting,
  shadowExecutionMode,
} from "./config.js";
import { valueOnCurve, tryPumpFunDirectSell } from "./pumpfun/route.js";
import { estimatePumpSwapSellQuote, warmPumpSwapStaticAccounts } from "./pumpswap/route.js";
import {
  getNewSwaps,
  getRecentWalletBuyMints,
  subscribeWalletSwaps,
  type Unsubscribe,
} from "./watcher.js";
import { subscribeGrpcWalletSwaps, type GrpcUnsubscribe } from "./grpcWatcher.js";
import { subscribeRabbitWalletSwaps, type RabbitUnsubscribe } from "./rabbitWatcher.js";
import { subscribeHeliusTransactionSwaps, type HeliusTxUnsubscribe } from "./heliusTxWatcher.js";
import {
  startRustPumpFunSidecar,
  type RustHotBuyFallback,
  type RustSidecarUnsubscribe,
} from "./rustSidecar.js";
import {
  mirrorSwap,
  NoPositionError,
  AlreadyBoughtError,
  ImpactTooHighError,
  SourceDriftError,
  BuyAmountBelowMinimumError,
  type Side,
  type MirrorResult,
} from "./mirror.js";
import { getQuote, getSwap, isJupiterRateLimitError } from "./jupiter.js";
import type { DetectedSwap, RustHotBuy } from "./types.js";
import {
  applyConfirmedTransactionState,
  applyContextualInventorySnapshot,
  authoritativeBalanceRaw,
  cappedQueuedSellRaw,
  emptyConfirmedInventoryLedger,
  shouldExpandQueuedSellAfterFailure,
  type ConfirmedInventoryLedger,
} from "./confirmedInventory.js";
import {
  confirmedPostSellRemainderRaw,
  shouldCancelPostSellRemainder,
  type PostSellRemainderIntent,
  type SourceSignalState,
} from "./postSellLifecycle.js";
import { warmConnection, createSenderTransactionFromSwapResponse, broadcastTransactionWithSender } from "./sender.js";
import { confirmSignature } from "./confirm.js";
import {
  getSolBalance,
  formatSol,
  getTokenBalance,
  getTokenBalanceFast,
  getTokenBalanceAtOrAfterSlot,
  getWalletTokenBalances,
  getWalletTokenBalanceSnapshot,
  type WalletTokenBalanceSnapshot,
} from "./balance.js";
import { telegram, type TradeNotification, type PnlNotification, formatPnlForTelegram } from "./telegram.js";
import "./telegramBot.js"; // Initialize Telegram bot handler
import {
  getCachedBalance,
  getCachedBalanceAgeMs,
  getBalanceInfo,
  hasEnoughSOLForTrade,
  isBalanceCriticallyLow,
  noteLocalSolCredit,
  noteLocalSolDebit,
  setBalanceCache,
  updateBalanceCache,
} from "./solReserve.js";
import {
  formatSolAmount,
  formatTokenAmount,
  formatMarketCap,
  getTokenMarketCap,
  getTokenMeta,
  shortMint,
  WRAPPED_SOL_MINT,
  USDC_MINT,
} from "./tokenRegistry.js";
import {
  recordBuy,
  adjustBuyTokenAmount,
  adjustBuySolCost,
  adjustSellExecution,
  recordSell,
  unrecordBuy,
  unrecordSell,
  unrecordZeroSell,
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
import { startJitoTipOracle, stopJitoTipOracle } from "./jitoTipOracle.js";
import { startJitoConnectionWarmup, stopJitoConnectionWarmup } from "./jitoWarmup.js";
import { classifyExecutionError } from "./errorClassifier.js";
import { decideRetryAction } from "./executionRetry.js";
import { isPostBroadcastBuyUncertainty, shouldHoldBuyOnceAfterFailure } from "./buyOncePolicy.js";
import { planExecution } from "./executionPlanner.js";
import { shouldUseBagsDirectLane } from "./bagsRoute.js";
import { recordExecutionTelemetrySync } from "./executionTelemetry.js";
import { warmWalletAccounts } from "./walletWarmup.js";
import { warmExecutionAltAccounts } from "./transactionSize.js";
import { startBlockhashCache, stopBlockhashCache } from "./blockhashCache.js";
import { validateLicense } from "./licenseClient.js";
import { checkPrebuyMoralisFilter } from "./prebuyMoralisFilter.js";
import { checkPrebuyInferredMarketCap } from "./prebuyInferredMarketCap.js";
import { applyShadowSellLamports, getShadowSolLamports, shadowCanSpend } from "./shadowExecution.js";
import {
  calculateProportionalSellRaw,
  recordSourceBuyRaw,
  recordSourceSellRaw,
} from "./sourcePositions.js";

const WARM_INTERVAL_MS = 30_000;
const BALANCE_LOG_INTERVAL_MS = 60_000;
const MAX_RECENT_SWAPS = 50;
const TOKEN_PROGRAM_IDS = [
  new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"),
  new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"),
];

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
  exitSignalSellsEnabled: boolean;
  exitSignalWallets: string[];
  simulationMode: boolean;
  buysPaused: boolean;
  maxOpenPositions: number | null;
  maxSolPerTokenSol: number | null;
  maxSessionDeployedSol: number | null;
  maxOpenDeployedSol: number | null;
  minBuySol: number;
  maxBuySol: number;
  copyBuyPercentage: number;
  solReserveSol: number;
  tipLamports: number;
  priorityFeeMicroLamports: number;
  mirrorSlippageBps: number;
  feeGuide: {
    computeUnitLimit: number;
    useJitoDirect: boolean;
    useJitoFallback: boolean;
    heliusTipMinLamports: number;
  };
  pollIntervalMs: number;
  riskControlsEnabled: boolean;
  riskCheckIntervalMs: number;
  minHoldBeforeStopLossMs: number;
  stopLossPct: number | null;
  takeProfitPct: number | null;
  lastBalanceLamports: number | null;
  lastBalanceSol: number | null;
  lastBalanceAt: number | null;
  sessionStartBalanceLamports: number | null;
  sessionWalletDeltaLamports: number | null;
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
const pendingBuyRetryByMint = new Map<string, DetectedSwap>();
const rustHotSniperPrimaryPendingMints = new Set<string>();
const rustHotSniperPrimaryFallbackSources = new Set<string>();
const rustHotBuyRecordedByMint = new Set<string>();
// Source signatures already booked from a Rust hot buy this session. Belt-and-suspenders
// against the Rust sidecar emitting the same hotBuy event twice (the primary guard is the
// per-source-signature gate in the sidecar, which stops the duplicate broadcast itself).
const rustHotBuyRecordedSourceSignatures = new Set<string>();
const fastBuyConfirmRetryScheduledByMint = new Set<string>();
const fastSellConfirmRetryScheduledByMint = new Set<string>();
const failedBuyRecoveryInFlightMints = new Set<string>();
const boughtMintsThisSession = new Set<string>();
const liveBuyAttemptedMintsThisSession = new Set<string>();
const persistentBuyLockDir = process.env.BUY_LOCK_DIR || join(tmpdir(), "finalbot-buy-locks");
const sourcePreexistingBuyMints = new Set<string>();
const sourceBuySignatureByMint = new Map<string, string>();
const mirrorBuySignatureByMint = new Map<string, string>();
const confirmedFastBuyTokensByMint = new Map<string, number>();
const pendingFastBuyConfirmSignaturesByMint = new Map<string, Set<string>>();
const fastBuyLocalCloseGuardUntilByMint = new Map<string, number>();
const loggedFastBuyDebitBySignature = new Set<string>();
const fastSellConfirmPendingByMint = new Set<string>();
const quarantinedBuyMints = new Map<string, string>();
const openPositionBuyMintsInFlight = new Set<string>();
const riskSellsInFlight = new Set<string>();
const exitSignalSellsInFlight = new Set<string>();
const riskSellRetryAfterByMint = new Map<string, number>();
const sourceHoldMissingByMint = new Map<string, number>();
const sourceSignalStateByMint = new Map<string, SourceSignalState>();
const confirmedInventoryByMint = new Map<string, ConfirmedInventoryLedger>();
const queuedSourceSellByMint = new Map<
  string,
  {
    swap: DetectedSwap;
    sourceVersion: number;
    targetRaw: number;
    retryAfter: number;
    attempts: number;
  }
>();
const queuedSellCapBySignature = new Map<string, number>();
const confirmedPostSellStateByMint = new Map<
  string,
  { signature: string; postTokenBalanceRaw: number; slot: number }
>();
const postSellRemainderChecksInFlight = new Set<string>();
const queuedPostSellRemainderByMint = new Map<
  string,
  { intent: PostSellRemainderIntent; attempt: number }
>();
const trackedSourceWalletSet = new Set(trackedWallets);
const RISK_SELL_FAILURE_COOLDOWN_MS = 15_000;
const ZERO_TOKEN_BALANCE_RECHECK_MS = 250;
const FAST_BUY_LOCAL_CLOSE_GUARD_MS = Math.max(90_000, confirmationNullStatusTimeoutMs * 3);
const POST_SELL_REMAINDER_SETTLE_MS = 750;
const POST_SELL_REMAINDER_MAX_ATTEMPTS = 4;
const SOURCE_SOLD_WHILE_BUY_PENDING_REASON = "source sold while buy was pending";
const GRADUATION_TRANSIENT_FLOOR_MS = 2 * 60_000;
const lastCurveRiskValueByMint = new Map<string, { outSolLamports: number; quotedAt: number }>();
type RiskTracePoint = {
  ts: number;
  valueLamports: number;
  costBasisLamports: number;
  drawdownPct: number;
  stopLossTrue: boolean;
};
const riskTraceByMint = new Map<string, RiskTracePoint[]>();

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isSourceSoldWhileBuyPendingQuarantine(reason: string | undefined): boolean {
  return reason === SOURCE_SOLD_WHILE_BUY_PENDING_REASON;
}

function markFastBuyConfirmationPending(mint: string, signature: string): void {
  let signatures = pendingFastBuyConfirmSignaturesByMint.get(mint);
  if (!signatures) {
    signatures = new Set<string>();
    pendingFastBuyConfirmSignaturesByMint.set(mint, signatures);
  }
  signatures.add(signature);
  extendFastBuyLocalCloseGuard(mint);
}

function clearFastBuyConfirmationPending(mint: string, signature?: string): void {
  if (!signature) {
    pendingFastBuyConfirmSignaturesByMint.delete(mint);
    drainQueuedSourceSell(mint);
    return;
  }
  const signatures = pendingFastBuyConfirmSignaturesByMint.get(mint);
  if (!signatures) return;
  signatures.delete(signature);
  if (signatures.size === 0) {
    pendingFastBuyConfirmSignaturesByMint.delete(mint);
    drainQueuedSourceSell(mint);
  }
}

function clearFastBuyConfirmationPendingSoon(mint: string, signature: string): void {
  const timer = setTimeout(() => {
    clearFastBuyConfirmationPending(mint, signature);
  }, 100);
  timer.unref?.();
}

function hasFastBuyConfirmationPending(mint: string): boolean {
  return (pendingFastBuyConfirmSignaturesByMint.get(mint)?.size ?? 0) > 0;
}

function extendFastBuyLocalCloseGuard(mint: string): void {
  const until = Date.now() + FAST_BUY_LOCAL_CLOSE_GUARD_MS;
  fastBuyLocalCloseGuardUntilByMint.set(
    mint,
    Math.max(fastBuyLocalCloseGuardUntilByMint.get(mint) ?? 0, until)
  );
}

function hasFastBuyLocalCloseGuard(mint: string): boolean {
  const until = fastBuyLocalCloseGuardUntilByMint.get(mint) ?? 0;
  if (until <= Date.now()) {
    fastBuyLocalCloseGuardUntilByMint.delete(mint);
    return false;
  }
  return true;
}

function shouldLogFastBuyDebit(signature: string): boolean {
  if (loggedFastBuyDebitBySignature.has(signature)) return false;
  loggedFastBuyDebitBySignature.add(signature);
  return true;
}

function clearFastSellConfirmationPendingSoon(mint: string): void {
  const timer = setTimeout(() => {
    releaseFastSellLifecycle(mint);
  }, 100);
  timer.unref?.();
}

function releaseFastSellLifecycle(mint: string): void {
  fastSellConfirmPendingByMint.delete(mint);
  drainQueuedSourceSell(mint);
}

function inventoryLedger(mint: string): ConfirmedInventoryLedger {
  return confirmedInventoryByMint.get(mint) ?? emptyConfirmedInventoryLedger();
}

function noteConfirmedInventory(
  mint: string,
  state: { slot: number; balanceRaw: number; signature: string }
): void {
  confirmedInventoryByMint.set(
    mint,
    applyConfirmedTransactionState(inventoryLedger(mint), state)
  );
}

async function refreshConfirmedInventory(mint: string): Promise<number> {
  if (!keypair) throw new Error("Wallet is not loaded.");
  const ledger = inventoryLedger(mint);
  const snapshot = await getTokenBalanceAtOrAfterSlot(
    keypair.publicKey,
    mint,
    ledger.refreshFloorSlot
  );
  const next = applyContextualInventorySnapshot(ledger, {
    slot: snapshot.contextSlot,
    balanceRaw: snapshot.amountRaw,
    signature: `account-snapshot-${snapshot.contextSlot}`,
  });
  confirmedInventoryByMint.set(mint, next);
  const amount = authoritativeBalanceRaw(next);
  if (amount == null) {
    throw new Error(`Could not establish confirmed inventory for ${mint}`);
  }
  return amount;
}

function queueSourceSell(swap: DetectedSwap, retryDelayMs = 0): void {
  const previous = queuedSourceSellByMint.get(swap.inputMint);
  const replayCap = queuedSellCapBySignature.get(swap.signature);
  queuedSellCapBySignature.delete(swap.signature);
  const targetRaw = Math.max(
    0,
    Math.floor(
      replayCap ??
        (previous?.swap.signature === swap.signature ? previous.targetRaw : undefined) ??
        getPosition(swap.inputMint)?.heldRaw ??
        0
    )
  );
  const attempts = retryDelayMs > 0 ? (previous?.attempts ?? 0) + 1 : previous?.attempts ?? 0;
  const backoff =
    retryDelayMs > 0
      ? Math.min(4_000, retryDelayMs * 2 ** Math.max(0, attempts - 1))
      : 0;
  queuedSourceSellByMint.set(swap.inputMint, {
    swap,
    sourceVersion: sourceSignalStateByMint.get(swap.inputMint)?.version ?? 0,
    targetRaw,
    retryAfter: Date.now() + backoff,
    attempts,
  });
}

function drainQueuedSourceSell(mint: string): void {
  if (stopRequested || !keypair || fastSellConfirmPendingByMint.has(mint)) return;
  const queued = queuedSourceSellByMint.get(mint);
  if (!queued) return;
  const waitMs = queued.retryAfter - Date.now();
  if (waitMs > 0) {
    const timer = setTimeout(() => drainQueuedSourceSell(mint), waitMs);
    timer.unref?.();
    return;
  }
  queuedSourceSellByMint.delete(mint);
  queuedSellCapBySignature.set(queued.swap.signature, queued.targetRaw);
  const timer = setTimeout(() => {
    if (!keypair || stopRequested) return;
    void processSwap(queued.swap, keypair).catch((err) => {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Queued source sell failed for ${mint.slice(0, 8)}...: ${msg}`);
    });
  }, 0);
  timer.unref?.();
}

function expandQueuedSellAfterFailure(mint: string): void {
  const queued = queuedSourceSellByMint.get(mint);
  if (!queued) return;
  if (
    shouldExpandQueuedSellAfterFailure(
      queued.sourceVersion,
      sourceSignalStateByMint.get(mint)
    )
  ) {
    queued.targetRaw = Math.max(queued.targetRaw, getPosition(mint)?.heldRaw ?? 0);
  }
}

function noteTrackedSourceMintSignal(
  wallet: string,
  mint: string,
  side: "buy" | "sell",
  signature: string
): void {
  if (!trackedSourceWalletSet.has(wallet)) return;
  const previous = sourceSignalStateByMint.get(mint);
  if (previous?.signature === signature) return;
  sourceSignalStateByMint.set(mint, {
    side,
    signature,
    version: (previous?.version ?? 0) + 1,
    observedAt: Date.now(),
  });
}

function noteTrackedSourceSignal(swap: DetectedSwap): void {
  const side = isBuySignal(swap) ? "buy" : isSellSignal(swap) ? "sell" : null;
  if (!side) return;
  noteTrackedSourceMintSignal(
    swap.wallet,
    side === "buy" ? swap.outputMint : swap.inputMint,
    side,
    swap.signature
  );
}

function sourceReenteredAfterFullSell(
  mint: string,
  intent: PostSellRemainderIntent
): boolean {
  return shouldCancelPostSellRemainder(sourceSignalStateByMint.get(mint), intent);
}

function schedulePostSellRemainderCheck(
  mint: string,
  intent: PostSellRemainderIntent,
  attempt = 1
): void {
  if (stopRequested || attempt > POST_SELL_REMAINDER_MAX_ATTEMPTS) {
    fastSellConfirmPendingByMint.delete(mint);
    return;
  }
  if (postSellRemainderChecksInFlight.has(mint)) {
    queuedPostSellRemainderByMint.set(mint, { intent, attempt: 1 });
    return;
  }
  postSellRemainderChecksInFlight.add(mint);

  const finish = (retry: boolean): void => {
    postSellRemainderChecksInFlight.delete(mint);
    const queued = queuedPostSellRemainderByMint.get(mint);
    if (queued) {
      queuedPostSellRemainderByMint.delete(mint);
      schedulePostSellRemainderCheck(mint, queued.intent, queued.attempt);
      return;
    }
    if (retry) schedulePostSellRemainderCheck(mint, intent, attempt + 1);
  };

  const timer = setTimeout(() => {
    void reconcilePostSellRemainder(mint, intent, attempt)
      .then(finish)
      .catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`Post-sell remainder check failed for ${mint.slice(0, 8)}...: ${msg}`);
        finish(true);
      });
  }, POST_SELL_REMAINDER_SETTLE_MS * attempt);
  timer.unref?.();
}

async function reconcilePostSellRemainder(
  mint: string,
  intent: PostSellRemainderIntent,
  attempt: number
): Promise<boolean> {
  if (!keypair || stopRequested) {
    releaseFastSellLifecycle(mint);
    return false;
  }
  if (sourceReenteredAfterFullSell(mint, intent)) {
    releaseFastSellLifecycle(mint);
    console.log(
      `Post-sell remainder drain canceled for ${mint.slice(0, 8)}...: ` +
        `fresh tracked-source buy arrived after the full-sell signal.`
    );
    return false;
  }

  const position = getPosition(mint);
  if (hasFastBuyConfirmationPending(mint)) {
    console.log(
      `Post-sell remainder check waiting for pending buy confirmation on ${mint.slice(0, 8)}... ` +
        `(${attempt}/${POST_SELL_REMAINDER_MAX_ATTEMPTS}).`
    );
    if (attempt >= POST_SELL_REMAINDER_MAX_ATTEMPTS) {
      releaseFastSellLifecycle(mint);
      return false;
    }
    return true;
  }

  const confirmedState = confirmedPostSellStateByMint.get(mint);
  const provenRemainderRaw = confirmedPostSellRemainderRaw(
    confirmedState?.postTokenBalanceRaw
  );
  if (provenRemainderRaw <= 0) {
    confirmedPostSellStateByMint.delete(mint);
    releaseFastSellLifecycle(mint);
    return false;
  }

  let liveRaw: number;
  try {
    liveRaw = await getVerifiedTokenBalance(keypair.publicKey, mint, {
      symbol: position?.symbol,
      expectedRaw: position?.heldRaw ?? 0,
      decimals: position?.decimals,
      ownerLabel: "our wallet",
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`Post-sell wallet balance read failed for ${mint.slice(0, 8)}...: ${msg}`);
    return attempt < POST_SELL_REMAINDER_MAX_ATTEMPTS;
  }

  if (liveRaw <= 0) {
    confirmedPostSellStateByMint.delete(mint);
    releaseFastSellLifecycle(mint);
    return false;
  }
  if (sourceReenteredAfterFullSell(mint, intent)) {
    confirmedPostSellStateByMint.delete(mint);
    releaseFastSellLifecycle(mint);
    return false;
  }

  const drainRaw = Math.min(liveRaw, provenRemainderRaw);
  confirmedPostSellStateByMint.delete(mint);
  fastSellConfirmPendingByMint.delete(mint);
  console.warn(
    `Confirmed post-sell remainder for ${mint.slice(0, 8)}...: ` +
      `${position ? formatTokenAmount(drainRaw, position.decimals) : drainRaw.toString()} remained in the confirmed sell transaction; draining proven amount.`
  );

  if (position && position.heldRaw > 0) {
    await sellLivePosition(
      position,
      "Post-sell remainder drain after confirmed full exit",
      drainRaw,
      false
    );
  } else {
    await sellLiveWalletTokenBalance(mint, drainRaw);
  }
  return false;
}

function buyLockPath(mint: string): string {
  return join(persistentBuyLockDir, `${mint.replace(/[^1-9A-HJ-NP-Za-km-z]/g, "_")}.lock`);
}

function createBuyLockOwner(): string {
  return `${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function acquirePersistentBuyLock(
  mint: string,
  sourceSignature: string
): { ok: true; owner?: string } | { ok: false; reason: string } {
  if (simulationMode || !buyOncePerTokenEnabled) return { ok: true };
  try {
    mkdirSync(persistentBuyLockDir, { recursive: true });
    const path = buyLockPath(mint);
    const fd = openSync(path, "wx");
    const owner = createBuyLockOwner();
    try {
      writeFileSync(
        fd,
        JSON.stringify({
          mint,
          sourceSignature,
          owner,
          pid: process.pid,
          createdAt: new Date().toISOString(),
        }) + "\n"
      );
    } finally {
      closeSync(fd);
    }
    return { ok: true, owner };
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "EEXIST") {
      return { ok: false, reason: `persistent buy lock exists at ${buyLockPath(mint)}` };
    }
    return { ok: false, reason: err instanceof Error ? err.message : String(err) };
  }
}

function releasePersistentBuyLock(mint: string): void {
  if (simulationMode || !buyOncePerTokenEnabled) return;
  try {
    unlinkSync(buyLockPath(mint));
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code !== "ENOENT") {
      console.warn(`Could not remove persistent buy lock for ${mint.slice(0, 8)}...: ${err}`);
    }
  }
}

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
const hybridExitTpDoneByMint = new Set<string>();
const hybridExitTpInFlightByMint = new Set<string>();
let running = false;
let startedAt: number | null = null;
let stopRequested = false;
let sleepResolve: (() => void) | null = null;
let keypair: Keypair | null = null;
let lastBalanceLamports: number | null = null;
let lastBalanceAt: number | null = null;
let sessionStartBalanceLamports: number | null = null;
let sessionStartBalanceAt: number | null = null;
let unsubscribeWs: Unsubscribe | null = null;
let unsubscribeGrpc: GrpcUnsubscribe | null = null;
let unsubscribeRabbit: RabbitUnsubscribe | null = null;
let unsubscribeRustSidecar: RustSidecarUnsubscribe | null = null;
let unsubscribeHeliusTx: HeliusTxUnsubscribe | null = null;
let riskPositionCursor = 0;

function trackedSourcePublicKeys(): PublicKey[] {
  return trackedWallets
    .map((wallet) => {
      try {
        return new PublicKey(wallet);
      } catch {
        console.warn(`Source-hold exit skipped invalid tracked wallet: ${wallet}`);
        return null;
      }
    })
    .filter((value): value is PublicKey => value != null);
}

async function quoteRiskExitLamports(mint: string, amountRaw: number): Promise<number> {
  if (!Number.isFinite(amountRaw) || amountRaw <= 0) return 0;
  if (pumpfunDirectEnabled) {
    const onCurve = await valueOnCurve(mint, amountRaw);
    if (onCurve != null) {
      lastCurveRiskValueByMint.set(mint, {
        outSolLamports: onCurve.solOut,
        quotedAt: Date.now(),
      });
      return onCurve.solOut;
    }

    const pumpSwapQuote = await estimatePumpSwapSellQuote(mint, amountRaw);
    if (pumpSwapQuote != null) return pumpSwapQuote;
  }

  try {
    const quote = await getQuote(mint, WRAPPED_SOL_MINT, amountRaw, 80);
    return parseInt(quote.outAmount ?? "0", 10);
  } catch (err) {
    const lastCurve = lastCurveRiskValueByMint.get(mint);
    if (
      isJupiterNoRoutesError(err) &&
      lastCurve &&
      Date.now() - lastCurve.quotedAt <= GRADUATION_TRANSIENT_FLOOR_MS
    ) {
      return lastCurve.outSolLamports;
    }
    throw err;
  }
}

function resolveTxAccountKeys(tx: VersionedTransactionResponse): string[] {
  const message = tx.transaction.message;
  const staticKeys = (
    "staticAccountKeys" in message
      ? message.staticAccountKeys
      : (message as unknown as { accountKeys: PublicKey[] }).accountKeys ?? []
  ) as PublicKey[];
  const loaded = tx.meta?.loadedAddresses;
  const writable = loaded?.writable ?? [];
  const readonly = loaded?.readonly ?? [];
  return [...staticKeys, ...writable, ...readonly].map((k) =>
    typeof k === "string" ? k : k.toBase58()
  );
}

type ConfirmedBuyExecution = {
  tokenAmountRaw: number;
  solSpentLamports: number | null;
  postTokenBalanceRaw: number;
  slot: number;
};

async function actualBuyExecutionFromTx(
  signature: string,
  mint: string
): Promise<ConfirmedBuyExecution | null> {
  if (!keypair) return null;
  try {
    const tx = await readConnection.getTransaction(signature, {
      commitment: "confirmed",
      maxSupportedTransactionVersion: 0,
    });
    if (!tx || tx.meta?.err) return null;
    const owner = keypair.publicKey.toBase58();
    const preByIndex = new Map<number, number>();
    for (const balance of tx.meta?.preTokenBalances ?? []) {
      if (balance.mint !== mint || balance.owner !== owner) continue;
      const raw = parseInt(balance.uiTokenAmount.amount ?? "0", 10);
      if (Number.isFinite(raw)) preByIndex.set(balance.accountIndex, raw);
    }

    let best = 0;
    let postTokenBalanceRaw = 0;
    for (const balance of tx.meta?.postTokenBalances ?? []) {
      if (balance.mint !== mint || balance.owner !== owner) continue;
      const raw = parseInt(balance.uiTokenAmount.amount ?? "0", 10);
      if (Number.isFinite(raw) && raw >= 0) postTokenBalanceRaw += raw;
      const pre = preByIndex.get(balance.accountIndex) ?? 0;
      const delta = raw - pre;
      if (Number.isFinite(delta) && delta > best) best = delta;
    }
    if (best <= 0) return null;
    const accountKeys = resolveTxAccountKeys(tx);
    const ownerIndex = accountKeys.indexOf(owner);
    const pre = ownerIndex >= 0 ? tx.meta?.preBalances?.[ownerIndex] : undefined;
    const post = ownerIndex >= 0 ? tx.meta?.postBalances?.[ownerIndex] : undefined;
    const solSpentLamports =
      typeof pre === "number" && typeof post === "number" && pre > post ? pre - post : null;
    return {
      tokenAmountRaw: best,
      solSpentLamports,
      postTokenBalanceRaw,
      slot: tx.slot,
    };
  } catch {
    return null;
  }
}

type ConfirmedSellExecution = {
  tokenAmountRaw: number;
  solReceivedLamports: number | null;
  postTokenBalanceRaw: number;
  slot: number;
};

async function actualSellExecutionFromTx(
  signature: string,
  mint: string
): Promise<ConfirmedSellExecution | null> {
  if (!keypair) return null;
  try {
    const tx = await readConnection.getTransaction(signature, {
      commitment: "confirmed",
      maxSupportedTransactionVersion: 0,
    });
    if (!tx || tx.meta?.err) return null;
    const owner = keypair.publicKey.toBase58();

    const preByIndex = new Map<number, number>();
    for (const balance of tx.meta?.preTokenBalances ?? []) {
      if (balance.mint !== mint || balance.owner !== owner) continue;
      const raw = parseInt(balance.uiTokenAmount.amount ?? "0", 10);
      if (Number.isFinite(raw)) preByIndex.set(balance.accountIndex, raw);
    }

    const seen = new Set<number>();
    let soldRaw = 0;
    let postTokenBalanceRaw = 0;
    for (const balance of tx.meta?.postTokenBalances ?? []) {
      if (balance.mint !== mint || balance.owner !== owner) continue;
      seen.add(balance.accountIndex);
      const pre = preByIndex.get(balance.accountIndex) ?? 0;
      const post = parseInt(balance.uiTokenAmount.amount ?? "0", 10);
      if (!Number.isFinite(post)) continue;
      postTokenBalanceRaw += post;
      if (pre > post) soldRaw += pre - post;
    }
    for (const [idx, pre] of preByIndex) {
      if (!seen.has(idx)) soldRaw += pre;
    }

    const accountKeys = resolveTxAccountKeys(tx);
    const ownerIndex = accountKeys.indexOf(owner);
    const pre = ownerIndex >= 0 ? tx.meta?.preBalances?.[ownerIndex] : undefined;
    const post = ownerIndex >= 0 ? tx.meta?.postBalances?.[ownerIndex] : undefined;
    const solReceivedLamports =
      typeof pre === "number" && typeof post === "number" && post > pre ? post - pre : null;

    if (soldRaw <= 0 && (solReceivedLamports == null || solReceivedLamports <= 0)) return null;
    return {
      tokenAmountRaw: soldRaw,
      solReceivedLamports,
      postTokenBalanceRaw,
      slot: tx.slot,
    };
  } catch {
    return null;
  }
}

async function actualTokensReceivedFromTx(signature: string, mint: string): Promise<number | null> {
  const execution = await actualBuyExecutionFromTx(signature, mint);
  return execution?.tokenAmountRaw ?? null;
}

async function reconcileFastBuyTokens(args: {
  mint: string;
  expectedTokenAmountRaw: number;
  expectedSolLamports: number;
  quoteMint?: string;
  signature: string;
}): Promise<void> {
  const execution = await actualBuyExecutionFromTx(args.signature, args.mint);
  if (execution == null || execution.tokenAmountRaw <= 0) return;
  noteConfirmedInventory(args.mint, {
    slot: execution.slot,
    balanceRaw: execution.postTokenBalanceRaw,
    signature: args.signature,
  });
  extendFastBuyLocalCloseGuard(args.mint);
  const actualTokens = execution.tokenAmountRaw;
  if (actualTokens !== args.expectedTokenAmountRaw && adjustBuyTokenAmount(args.mint, args.expectedTokenAmountRaw, actualTokens)) {
    confirmedFastBuyTokensByMint.set(args.mint, actualTokens);
    console.warn(
      `🧮 Fast buy token amount corrected for ${args.mint.slice(0, 8)}…: ` +
        `${formatTokenAmount(args.expectedTokenAmountRaw, 6)} quoted → ${formatTokenAmount(actualTokens, 6)} actual.`
    );
  }
  if (
    args.quoteMint !== USDC_MINT &&
    useWalletPnlAccounting &&
    execution.solSpentLamports != null &&
    execution.solSpentLamports > 0 &&
    execution.solSpentLamports !== args.expectedSolLamports &&
    adjustBuySolCost(args.mint, args.expectedSolLamports, execution.solSpentLamports)
  ) {
    console.warn(
      `🧾 Fast buy cost basis corrected for ${args.mint.slice(0, 8)}…: ` +
        `${formatSolAmount(args.expectedSolLamports)} SOL trade → ${formatSolAmount(execution.solSpentLamports)} SOL wallet debit.`
    );
  } else if (
    args.quoteMint !== USDC_MINT &&
    !useWalletPnlAccounting &&
    execution.solSpentLamports != null &&
    execution.solSpentLamports > 0 &&
    execution.solSpentLamports !== args.expectedSolLamports
  ) {
    if (!shouldLogFastBuyDebit(args.signature)) return;
    console.warn(
      `🧾 Fast buy wallet debit observed for ${args.mint.slice(0, 8)}…: ` +
        `${formatSolAmount(execution.solSpentLamports)} SOL all-in vs ${formatSolAmount(args.expectedSolLamports)} SOL trade basis ` +
        `(PnL basis=${pnlAccountingBasis}).`
    );
  }
}

async function reconcileOpenPositionWithConfirmedBuy(
  position: PositionSnapshot
): Promise<PositionSnapshot> {
  const signature = mirrorBuySignatureByMint.get(position.mint);
  if (!signature || position.buysCount <= 0) return position;
  // mirrorBuySignatureByMint only holds the most recent buy's signature, so the
  // single-tx execution lookup below represents ONE buy. For a multi-buy (DCA)
  // position, comparing that one buy's tokens/cost against the position TOTAL would
  // wrongly shrink it to a single buy's size — and a later "sell all" sells
  // min(live, tracked), stranding the rest on-chain (observed on $$SLY: live
  // 736,939 vs clobbered tracked 366,909). Each buy is already reconciled
  // individually via reconcileFastBuyTokens at its own confirmation, so skip the
  // position-level reconcile once more than one buy is recorded.
  if (position.buysCount > 1) return position;

  const execution = await actualBuyExecutionFromTx(signature, position.mint);
  if (execution == null || execution.tokenAmountRaw <= 0) return position;
  noteConfirmedInventory(position.mint, {
    slot: execution.slot,
    balanceRaw: execution.postTokenBalanceRaw,
    signature,
  });
  extendFastBuyLocalCloseGuard(position.mint);

  let changed = false;
  if (
    execution.tokenAmountRaw !== position.totalBoughtRaw &&
    adjustBuyTokenAmount(position.mint, position.totalBoughtRaw, execution.tokenAmountRaw)
  ) {
    confirmedFastBuyTokensByMint.set(position.mint, execution.tokenAmountRaw);
    changed = true;
    console.warn(
      `Fast buy reconciled before risk/sell for ${position.mint.slice(0, 8)}...: ` +
        `${formatTokenAmount(position.totalBoughtRaw, position.decimals)} tracked -> ` +
        `${formatTokenAmount(execution.tokenAmountRaw, position.decimals)} actual.`
    );
  }

  if (
    execution.solSpentLamports != null &&
    execution.solSpentLamports > 0 &&
    useWalletPnlAccounting &&
    execution.solSpentLamports !== position.totalSolSpentLamports &&
    adjustBuySolCost(position.mint, position.totalSolSpentLamports, execution.solSpentLamports)
  ) {
    changed = true;
    console.warn(
      `Fast buy cost reconciled before risk/sell for ${position.mint.slice(0, 8)}...: ` +
        `${formatSolAmount(position.totalSolSpentLamports)} SOL tracked -> ` +
        `${formatSolAmount(execution.solSpentLamports)} SOL wallet debit.`
    );
  } else if (
    execution.solSpentLamports != null &&
    execution.solSpentLamports > 0 &&
    !useWalletPnlAccounting &&
    execution.solSpentLamports !== position.totalSolSpentLamports
  ) {
    if (!shouldLogFastBuyDebit(signature)) return changed ? getPosition(position.mint) ?? position : position;
    console.warn(
      `Fast buy wallet debit observed for ${position.mint.slice(0, 8)}...: ` +
        `${formatSolAmount(execution.solSpentLamports)} SOL all-in vs ` +
        `${formatSolAmount(position.totalSolSpentLamports)} SOL trade basis (PnL basis=${pnlAccountingBasis}).`
    );
  }

  return changed ? getPosition(position.mint) ?? position : position;
}

async function reconcileFastSellExecution(args: {
  mint: string;
  expectedTokenAmountRaw: number;
  expectedSolLamports: number;
  signature: string;
}): Promise<ConfirmedSellExecution | null> {
  const execution = await actualSellExecutionFromTx(args.signature, args.mint);
  if (execution == null) return null;
  const actualTokens =
    execution.tokenAmountRaw > 0 ? execution.tokenAmountRaw : args.expectedTokenAmountRaw;
  const actualSol =
    execution.solReceivedLamports != null && execution.solReceivedLamports >= 0
      ? execution.solReceivedLamports
      : args.expectedSolLamports;
  const ledgerSol = useWalletPnlAccounting ? actualSol : args.expectedSolLamports;
  if (
    actualTokens !== args.expectedTokenAmountRaw ||
    actualSol !== args.expectedSolLamports
  ) {
    const adjusted = adjustSellExecution(
      args.mint,
      args.expectedTokenAmountRaw,
      actualTokens,
      args.expectedSolLamports,
      ledgerSol
    );
    if (adjusted) {
      console.warn(
        `🧾 Fast sell corrected for ${args.mint.slice(0, 8)}…: ` +
          `${formatTokenAmount(args.expectedTokenAmountRaw, 6)} / ${formatSolAmount(args.expectedSolLamports)} SOL quoted → ` +
          `${formatTokenAmount(actualTokens, 6)} / ${formatSolAmount(actualSol)} SOL actual wallet delta` +
          (!useWalletPnlAccounting ? ` (kept ${formatSolAmount(args.expectedSolLamports)} SOL DEX/trade PnL basis).` : ".")
      );
      const pos = getPosition(args.mint);
      const avgEntry = pos && pos.totalBoughtRaw > 0 ? pos.totalSolSpentLamports / pos.totalBoughtRaw : 0;
      const fillPnl =
        avgEntry > 0 ? ledgerSol - avgEntry * actualTokens : ledgerSol - args.expectedSolLamports;
      void telegram.notifyInfo(
        `Sell accounting corrected for $${pos?.symbol ?? shortMint(args.mint)}`,
        [
          `Tx: ${args.signature}`,
          `Quoted: ${formatSolAmount(args.expectedSolLamports)} SOL`,
          `Confirmed wallet delta: ${formatSolAmount(actualSol)} SOL`,
          !useWalletPnlAccounting ? `PnL basis kept as DEX/trade quote: ${formatSolAmount(args.expectedSolLamports)} SOL` : undefined,
          `Fill PnL now: ${formatPnl(fillPnl)}`,
          pos ? `Position PnL now: ${formatPnl(pos.realizedPnlLamports)}` : undefined,
        ]
          .filter(Boolean)
          .join("\n")
      );
    } else if (!useWalletPnlAccounting && actualSol !== args.expectedSolLamports) {
      console.warn(
        `🧾 Fast sell wallet delta observed for ${args.mint.slice(0, 8)}…: ` +
          `${formatSolAmount(actualSol)} SOL all-in vs ${formatSolAmount(args.expectedSolLamports)} SOL DEX/trade basis ` +
          `(PnL basis=${pnlAccountingBasis}).`
      );
    }
  }
  confirmedPostSellStateByMint.set(args.mint, {
    signature: args.signature,
    postTokenBalanceRaw: execution.postTokenBalanceRaw,
    slot: execution.slot,
  });
  noteConfirmedInventory(args.mint, {
    slot: execution.slot,
    balanceRaw: execution.postTokenBalanceRaw,
    signature: args.signature,
  });
  return execution;
}
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

let buysPaused = false;

export function areBuysPaused(): boolean {
  return buysPaused;
}

export function setBuysPaused(paused: boolean): void {
  if (buysPaused === paused) return;
  buysPaused = paused;
  console.log(
    paused
      ? "⏸️ Buys PAUSED — no new entries; open positions are still managed and sold."
      : "▶️ Buys RESUMED — new entries re-enabled."
  );
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
  positionRealizedPnlLamports?: number;
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
    positionRealizedPnlLamports: extras?.positionRealizedPnlLamports ?? null,
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

async function updateBalance(pubkey: PublicKey, options: { log?: boolean } = {}): Promise<void> {
  const shouldLog = options.log ?? true;
  try {
    const shadowLamports = getShadowSolLamports();
    if (simulationMode && shadowLamports != null) {
      lastBalanceLamports = shadowLamports;
      lastBalanceAt = Date.now();
      if (shouldLog) console.log("Balance:", formatSol(shadowLamports), "(shadow simulated)");
      return;
    }
    if (simulationMode && simulationSolBalance != null) {
      const lamports = Math.round(simulationSolBalance * LAMPORTS_PER_SOL);
      lastBalanceLamports = lamports;
      lastBalanceAt = Date.now();
      if (shouldLog) console.log("Balance:", formatSol(lamports), "(simulated)");
      return;
    }
    const { lamports } = await getSolBalance(pubkey);
    lastBalanceLamports = lamports;
    lastBalanceAt = Date.now();
    setBalanceCache(lamports);
    if (shouldLog) console.log("Balance:", formatSol(lamports));
  } catch (err) {
    console.warn("Failed to fetch balance:", err);
  }
}

function captureSessionStartBalance(): void {
  sessionStartBalanceLamports = lastBalanceLamports;
  sessionStartBalanceAt = lastBalanceLamports != null ? Date.now() : null;
}

export type SessionWalletPnlSnapshot = {
  startBalanceLamports: number | null;
  currentBalanceLamports: number | null;
  deltaLamports: number | null;
  startBalanceAt: number | null;
  currentBalanceAt: number | null;
};

export function getSessionWalletPnlSnapshot(): SessionWalletPnlSnapshot {
  const deltaLamports =
    sessionStartBalanceLamports != null && lastBalanceLamports != null
      ? lastBalanceLamports - sessionStartBalanceLamports
      : null;
  return {
    startBalanceLamports: sessionStartBalanceLamports,
    currentBalanceLamports: lastBalanceLamports,
    deltaLamports,
    startBalanceAt: sessionStartBalanceAt,
    currentBalanceAt: lastBalanceAt,
  };
}

function legDisplay(leg: {
  meta: { mint: string; symbol: string; decimals: number };
  amountRaw: number;
}): string {
  if (leg.meta.mint === WRAPPED_SOL_MINT) {
    return `${formatSolAmount(leg.amountRaw)} SOL`;
  }
  if (leg.meta.mint === USDC_MINT) {
    return `${(leg.amountRaw / 1_000_000).toFixed(2)} USDC`;
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

function pnlArrow(lamports: number): string {
  if (lamports > 0) return "+";
  if (lamports < 0) return "-";
  return "";
}

export function formatPnl(lamports: number): string {
  const abs = Math.abs(lamports);
  return `${pnlArrow(lamports)}${formatSolAmount(abs)} SOL`;
}

type SellFillAccounting = {
  costedRaw: number;
  uncostedRaw: number;
  attributedProceedsLamports: number;
  costBasisLamports: number;
  pnlLamports: number;
  pnlPct: number | undefined;
};

function sellFillAccounting(
  position: PositionSnapshot | null,
  soldRaw: number,
  proceedsLamports: number
): SellFillAccounting {
  const heldRaw = Math.max(0, position?.heldRaw ?? 0);
  const costedRaw = Math.min(Math.max(0, soldRaw), heldRaw);
  const uncostedRaw = Math.max(0, soldRaw - costedRaw);
  const avgEntryLamportsPerRaw =
    position && position.totalBoughtRaw > 0 ? position.totalSolSpentLamports / position.totalBoughtRaw : 0;
  const attributedProceedsLamports =
    soldRaw > 0 && costedRaw < soldRaw
      ? Math.floor((proceedsLamports * costedRaw) / soldRaw)
      : proceedsLamports;
  const costBasisLamports = avgEntryLamportsPerRaw * costedRaw;
  const pnlLamports = attributedProceedsLamports - costBasisLamports;
  return {
    costedRaw,
    uncostedRaw,
    attributedProceedsLamports,
    costBasisLamports,
    pnlLamports,
    pnlPct: costBasisLamports > 0 ? (pnlLamports / costBasisLamports) * 100 : undefined,
  };
}

function shouldQuarantineFailedBuy(reason: string): boolean {
  return decideRetryAction(reason).shouldQuarantine;
}

function holdBuyOnceMintAfterBroadcastUncertainty(mint: string, reason: string): void {
  if (!buyOncePerTokenEnabled) return;
  liveBuyAttemptedMintsThisSession.add(mint);
  boughtMintsThisSession.add(mint);
  pendingBuyRetryByMint.delete(mint);
  fastBuyConfirmRetryScheduledByMint.delete(mint);
  failedBuyRecoveryInFlightMints.delete(mint);
  quarantinedBuyMints.set(mint, `broadcast status uncertain: ${reason}`);
  console.warn(
    `Buy lock preserved for ${mint.slice(0, 8)}... after broadcast/confirmation uncertainty. ` +
      "No automatic retry for this mint this session."
  );
}

function isPumpFunAccountLayoutFailure(reason: string): boolean {
  const category = classifyExecutionError(reason).category;
  return (
    category === "account-layout" ||
    category === "account-constraint-seeds" ||
    category === "account-constraint-mut" ||
    category === "account-invalid-program" ||
    category === "account-privilege" ||
    category === "account-stale-source"
  );
}

function isBuySignal(swap: DetectedSwap): boolean {
  return (
    (swap.inputMint === WRAPPED_SOL_MINT || swap.inputMint === USDC_MINT) &&
    swap.outputMint !== WRAPPED_SOL_MINT &&
    swap.outputMint !== USDC_MINT
  );
}

function isSellSignal(swap: DetectedSwap): boolean {
  return swap.outputMint === WRAPPED_SOL_MINT || swap.outputMint === USDC_MINT;
}

function recordMirrorSkip(
  swap: DetectedSwap,
  reason: string,
  details: Record<string, unknown> = {}
): void {
  const isSell = isSellSignal(swap);
  recordExecutionTelemetrySync({
    kind: "mirror-skip",
    route: "skip",
    side: isSell ? "sell" : isBuySignal(swap) ? "buy" : "swap",
    mint: isSell ? swap.inputMint : isBuySignal(swap) ? swap.outputMint : swap.outputMint,
    sourceSignature: swap.signature,
    wallet: swap.wallet,
    detector: swap.detector,
    success: false,
    errorSummary: reason,
    details,
  });
}

function sourceBuyTokenAmountRaw(swap: DetectedSwap): number {
  if (!isBuySignal(swap)) return 0;
  const raw = swap.counterpartyAmountRaw ?? 0;
  return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 0;
}

function sourceSellTokenAmountRaw(swap: DetectedSwap): number {
  if (!isSellSignal(swap) || swap.inputMint === WRAPPED_SOL_MINT || swap.inputMint === USDC_MINT) return 0;
  const raw = swap.amountRaw;
  return Number.isFinite(raw) && raw > 0 ? Math.floor(raw) : 0;
}

function quoteAmountDisplay(raw: number, mint: string): string {
  if (mint === USDC_MINT) return `${(raw / 1_000_000).toFixed(2)} USDC`;
  return `${(raw / LAMPORTS_PER_SOL).toFixed(4)} SOL`;
}

function mirrorQuoteMintForBuy(swap: DetectedSwap): string {
  if (mirrorQuoteAsset === "SOL") return WRAPPED_SOL_MINT;
  if (mirrorQuoteAsset === "USDC") return USDC_MINT;
  return swap.inputMint === USDC_MINT ? USDC_MINT : WRAPPED_SOL_MINT;
}

function mirroredBuyAmountRaw(swap: DetectedSwap): number | null {
  const quoteMint = mirrorQuoteMintForBuy(swap);
  const calculatedRaw = Math.floor(swap.amountRaw * (copyBuyPercentage / 100));
  if (calculatedRaw <= 0) return null;
  if (quoteMint === USDC_MINT) {
    if (calculatedRaw < minBuyUsdcRaw) {
      if (minBuyBehavior === "skip") return null;
      if (minBuyBehavior === "floor") return Math.min(maxBuyUsdcRaw, minBuyUsdcRaw);
    }
    return Math.min(maxBuyUsdcRaw, calculatedRaw);
  }
  if (calculatedRaw < minBuyLamports) {
    if (minBuyBehavior === "skip") return null;
    if (minBuyBehavior === "floor") return Math.min(maxBuyLamports, minBuyLamports);
  }
  return Math.min(maxBuyLamports, calculatedRaw);
}

function shouldDeferToRustHotSniperPrimary(
  swap: DetectedSwap,
  executionPlan: ReturnType<typeof planExecution>
): boolean {
  if (!rustHotSniperPrimary || !rustHotSniperEnabled || !rustSidecarEnabled) return false;
  if (simulationMode || shadowExecutionMode) return false;
  if (rustHotSniperPrimaryFallbackSources.has(swap.signature)) return false;
  if (!isBuySignal(swap) || executionPlan.side !== "buy") return false;
  if (swap.inputMint !== WRAPPED_SOL_MINT) return false;
  if (swap.skipDirectBuyRoutes || swap.forceDerivedPumpFunBuy) return false;
  return executionPlan.route === "pumpfun-bonding-curve" || executionPlan.route === "pumpfun-v2-token2022";
}

async function handleRustHotBuy(hotBuy: RustHotBuy, kp: Keypair): Promise<void> {
  if (stopRequested) return;
  noteTrackedSourceMintSignal(hotBuy.wallet, hotBuy.mint, "buy", hotBuy.sourceSignature);
  if (rustHotBuyRecordedSourceSignatures.has(hotBuy.sourceSignature)) {
    console.log(
      `Skipping duplicate Rust hot buy bookkeeping for ${hotBuy.mint.slice(0, 8)}...; ` +
        `source tx ${hotBuy.sourceSignature.slice(0, 8)}... already recorded this session.`
    );
    return;
  }
  rustHotBuyRecordedSourceSignatures.add(hotBuy.sourceSignature);
  const quarantineReason = quarantinedBuyMints.get(hotBuy.mint);
  if (isSourceSoldWhileBuyPendingQuarantine(quarantineReason)) {
    quarantinedBuyMints.delete(hotBuy.mint);
    console.log(
      `Rust hot buy superseded stale source-sold quarantine for ${hotBuy.mint.slice(0, 8)}...; recording broadcast.`
    );
  }
  const ts = Date.now();
  const route = hotBuy.route ?? "pumpfun-hot-source-replay-buy";
  const sourceSwap: DetectedSwap = {
    signature: hotBuy.sourceSignature,
    wallet: hotBuy.wallet,
    detector: "rust-hot-sniper",
    receivedAtMs: hotBuy.receivedAtMs,
    detectedAtMs: hotBuy.detectedAtMs,
    sourceSlot: hotBuy.sourceSlot,
    sourceTransactionIndex: hotBuy.sourceTransactionIndex,
    inputMint: WRAPPED_SOL_MINT,
    outputMint: hotBuy.mint,
    amountRaw: hotBuy.sourceAmountRaw,
    isNativeInput: true,
  };

  recordExecutionTelemetrySync({
    kind: "source-detected",
    route,
    side: "buy",
    mint: hotBuy.mint,
    sourceSignature: hotBuy.sourceSignature,
    wallet: hotBuy.wallet,
    detector: "rust-hot-sniper",
    elapsedMs:
      hotBuy.detectedAtMs != null && hotBuy.receivedAtMs != null
        ? hotBuy.receivedAtMs - hotBuy.detectedAtMs
        : hotBuy.elapsedMs,
    success: true,
    details: {
      sourceSlot: hotBuy.sourceSlot,
      sourceTransactionIndex: hotBuy.sourceTransactionIndex,
      rustElapsedMs: hotBuy.elapsedMs,
      senderPath: hotBuy.senderPath,
    },
  });
  recordExecutionTelemetrySync({
    kind: "execution-plan",
    route,
    side: "buy",
    mint: hotBuy.mint,
    sourceSignature: hotBuy.sourceSignature,
    wallet: hotBuy.wallet,
    detector: "rust-hot-sniper",
    elapsedMs: 0,
    success: true,
    details: { reason: "rust-hot-sniper pre-node broadcast" },
  });
  if (hotBuy.buildMs != null) {
    recordExecutionTelemetrySync({
      kind: "stage:build",
      route,
      side: "buy",
      mint: hotBuy.mint,
      sourceSignature: hotBuy.sourceSignature,
      wallet: hotBuy.wallet,
      detector: "rust-hot-sniper",
      elapsedMs: hotBuy.buildMs,
      success: true,
      details: { txBytes: hotBuy.txBytes },
    });
  }
  recordExecutionTelemetrySync({
    kind: "broadcast",
    route,
    side: "buy",
    mint: hotBuy.mint,
    sourceSignature: hotBuy.sourceSignature,
    signature: hotBuy.signature,
    wallet: hotBuy.wallet,
    detector: "rust-hot-sniper",
    elapsedMs: hotBuy.broadcastMs ?? hotBuy.elapsedMs,
    success: true,
    details: { senderPath: hotBuy.senderPath, txBytes: hotBuy.txBytes },
  });

  const inputMeta = await getTokenMeta(WRAPPED_SOL_MINT);
  const outputMeta = await getTokenMeta(hotBuy.mint);
  const result: MirrorResult = {
    signature: hotBuy.signature,
    isSimulated: false,
    side: "buy",
    input: { meta: inputMeta, amountRaw: hotBuy.solInLamports },
    output: { meta: outputMeta, amountRaw: hotBuy.tokensOutRaw },
    costBasisLamports: hotBuy.solInLamports,
    priceImpactPct: "0",
    confirmationPending: true,
  };

  console.log(
    `Rust hot sniper recorded buy for ${hotBuy.mint.slice(0, 8)}... ` +
      `(${formatSolAmount(hotBuy.solInLamports)} SOL, ${hotBuy.elapsedMs ?? "?"}ms, ${hotBuy.senderPath ?? "unknown sender"}).`
  );
  liveBuyAttemptedMintsThisSession.add(hotBuy.mint);
  rustHotBuyRecordedByMint.add(hotBuy.mint);
  rustHotSniperPrimaryPendingMints.delete(hotBuy.mint);
  const marketCapUsd = await maybeGetMarketCap(hotBuy.mint);
  const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;
  logTrade(result, sourceSwap, marketCapUsd, marketCapCheckedAt);
  noteLocalSolDebit(hotBuy.solInLamports);

  pushSwap(
    {
      ts,
      sourceSignature: hotBuy.sourceSignature,
      wallet: hotBuy.wallet,
      side: "buy",
      input: toLegView(result.input),
      output: toLegView(result.output),
      isSimulated: false,
      mirrorSignature: hotBuy.signature,
      marketCapUsd,
      marketCapCheckedAt,
    },
    { priceImpactPct: 0 }
  );

  const confirmStartedAt = Date.now();
  confirmSignature(hotBuy.signature)
    .then(async () => {
      recordExecutionTelemetrySync({
        kind: "stage:confirm",
        route,
        side: "buy",
        mint: hotBuy.mint,
        sourceSignature: hotBuy.sourceSignature,
        signature: hotBuy.signature,
        wallet: hotBuy.wallet,
        detector: "rust-hot-sniper",
        elapsedMs: Date.now() - confirmStartedAt,
        success: true,
        details: { mode: "background", senderPath: hotBuy.senderPath },
      });
      pendingBuyRetryByMint.delete(hotBuy.mint);
      confirmedFastBuyTokensByMint.set(hotBuy.mint, hotBuy.tokensOutRaw);
      try {
        await reconcileFastBuyTokens({
          mint: hotBuy.mint,
          expectedTokenAmountRaw: hotBuy.tokensOutRaw,
          expectedSolLamports: hotBuy.solInLamports,
          quoteMint: WRAPPED_SOL_MINT,
          signature: hotBuy.signature,
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`Fast Rust hot buy reconciliation failed for ${hotBuy.mint.slice(0, 8)}…: ${msg}`);
      }
      clearFastBuyConfirmationPendingSoon(hotBuy.mint, hotBuy.signature);
    })
    .catch((err) => {
      const reason = err instanceof Error ? err.message : String(err);
      recordExecutionTelemetrySync({
        kind: "stage:confirm",
        route,
        side: "buy",
        mint: hotBuy.mint,
        sourceSignature: hotBuy.sourceSignature,
        signature: hotBuy.signature,
        wallet: hotBuy.wallet,
        detector: "rust-hot-sniper",
        elapsedMs: Date.now() - confirmStartedAt,
        success: false,
        error: reason,
        errorClass: classifyExecutionError(reason).category,
        errorCode: classifyExecutionError(reason).code,
        details: { mode: "background", senderPath: hotBuy.senderPath },
      });
      const removed = unrecordBuy(hotBuy.mint, hotBuy.tokensOutRaw, hotBuy.solInLamports);
      mirrorBuySignatureByMint.delete(hotBuy.mint);
      clearFastBuyConfirmationPending(hotBuy.mint, hotBuy.signature);
      confirmedFastBuyTokensByMint.delete(hotBuy.mint);
      rustHotBuyRecordedByMint.delete(hotBuy.mint);
      noteLocalSolCredit(hotBuy.solInLamports);
      rustHotSniperPrimaryPendingMints.delete(hotBuy.mint);
      if (removed) {
        console.warn(
          `Phantom Rust hot buy cleaned up: ${hotBuy.mint.slice(0, 8)}... ` +
            `(reason: ${reason}). Position removed from tracking.`
        );
      }
      const decision = decideRetryAction(reason);
      if (shouldHoldBuyOnceAfterFailure({ buyOncePerTokenEnabled, reason })) {
        holdBuyOnceMintAfterBroadcastUncertainty(hotBuy.mint, reason);
        return;
      }
      if (decision.shouldQuarantine) {
        pendingBuyRetryByMint.delete(hotBuy.mint);
        quarantinedBuyMints.set(hotBuy.mint, `rust hot buy ${decision.action}: ${reason}`);
        console.warn(
          `Buy quarantine set for ${hotBuy.mint.slice(0, 8)}... after Rust hot buy failed ` +
            `(${reason}). No more retries for this mint this session.`
        );
      } else {
        const fallback = pendingBuyRetryByMint.get(hotBuy.mint);
        pendingBuyRetryByMint.delete(hotBuy.mint);
        failedBuyRecoveryInFlightMints.delete(hotBuy.mint);
        boughtMintsThisSession.delete(hotBuy.mint);
        liveBuyAttemptedMintsThisSession.delete(hotBuy.mint);
        if (shouldAttemptFailedBuyRecovery(fallback, reason)) {
          console.warn(
            `Rust hot buy failed for ${hotBuy.mint.slice(0, 8)}... with recoverable ` +
              `${decision.category} failure; retrying queued TypeScript fallback now.`
          );
          scheduleFailedBuyRecovery(fallback, kp, reason);
        } else {
          console.warn(
            `Rust hot buy failed for ${hotBuy.mint.slice(0, 8)}... with recoverable ` +
              `${decision.category} failure; a later source signal can retry.`
          );
        }
      }
    });
}

function isTerminalRustHotFallback(reason: string): boolean {
  const normalized = reason.toLowerCase();
  return (
    normalized.includes("rust hot sniper mc ") ||
    normalized.includes("rust hot sniper mc unavailable") ||
    normalized.includes("rust hot sniper mc needs") ||
    normalized.includes("source instruction accounts unavailable") ||
    normalized.includes("source instruction data unavailable") ||
    normalized.includes("rust executor returned no signature") ||
    normalized.includes("blockhash fetch failed") ||
    normalized.includes("copy-sized amount below minimum") ||
    normalized.includes("copy-sized amount is zero") ||
    normalized.includes("session deployed sol cap would be exceeded")
  );
}

function handleRustHotBuyFallback(fallback: RustHotBuyFallback): void {
  rustHotSniperPrimaryPendingMints.delete(fallback.mint);
  failedBuyRecoveryInFlightMints.delete(fallback.mint);
  const terminal = isTerminalRustHotFallback(fallback.reason);
  if (terminal || rustOnlyBuys) {
    pendingBuyRetryByMint.delete(fallback.mint);
    console.log(
      `Rust hot sniper ${terminal ? "terminal skip" : "fallback"} for ${fallback.mint.slice(0, 8)}...; ` +
        `cleared primary pending state (${fallback.reason}).`
    );
  }
}

async function getCurrentSourceHeldMints(wallet: string): Promise<Set<string>> {
  const owner = new PublicKey(wallet);
  const mints = new Set<string>();

  for (const programId of TOKEN_PROGRAM_IDS) {
    const resp = await readConnection.getParsedTokenAccountsByOwner(owner, { programId });
    for (const item of resp.value) {
      const info = item.account.data.parsed?.info as
        | { mint?: string; tokenAmount?: { amount?: string } }
        | undefined;
      const mint = info?.mint;
      const amount = info?.tokenAmount?.amount;
      if (!mint || mint === WRAPPED_SOL_MINT || mint === USDC_MINT) continue;
      if (amount && BigInt(amount) > 0n) mints.add(mint);
    }
  }

  return mints;
}

async function warmSourcePreexistingBuyMints(startupCutoffSec: number): Promise<void> {
  sourcePreexistingBuyMints.clear();
  if (!skipSourcePreexistingMints) return;

  const started = Date.now();
  console.log(
    `Preloading source preexisting mints: current holdings + ${sourcePreexistingScanTxLimit} recent tx(s)/wallet...`
  );

  for (const wallet of trackedWallets) {
    try {
      const [heldMints, recentBuyMints] = await Promise.all([
        getCurrentSourceHeldMints(wallet),
        getRecentWalletBuyMints(wallet, sourcePreexistingScanTxLimit, startupCutoffSec),
      ]);

      for (const mint of heldMints) sourcePreexistingBuyMints.add(mint);
      for (const mint of recentBuyMints) sourcePreexistingBuyMints.add(mint);

      console.log(
        `  ${wallet.slice(0, 4)}...${wallet.slice(-4)}: ` +
          `${heldMints.size} held, ${recentBuyMints.size} recent buy mint(s).`
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Source preexisting preload skipped for ${wallet.slice(0, 6)}...: ${msg}`);
    }
  }

  console.log(
    `Source preexisting skip loaded ${sourcePreexistingBuyMints.size} mint(s) in ${Date.now() - started}ms.`
  );
}

function shouldAttemptFailedBuyRecovery(
  sourceSwap: DetectedSwap | undefined,
  reason: string
): sourceSwap is DetectedSwap {
  const decision = decideRetryAction(reason);
  const canRescue =
    decision.action === "rebuild-derived-accounts" ||
    decision.action === "rebuild-pool-state" ||
    decision.action === "fresh-quote" ||
    decision.action === "compact-transaction" ||
    decision.action === "fresh-blockhash-rebroadcast";
  const mint = sourceSwap?.outputMint;
  if (
    canRescue &&
    Boolean(sourceSwap) &&
    Boolean(mint) &&
    !failedBuyRecoveryInFlightMints.has(mint!) &&
    !sourceSwap?.skipDirectBuyRoutes &&
    !sourceSwap?.forceDerivedPumpFunBuy &&
    (sourceSwap?.buyRecoveryAttempt ?? 0) < executionRescueRetries
  ) {
    return true;
  }
  return (
    failedBuyRecovery === "JUPITER_ONCE" &&
    Boolean(sourceSwap) &&
    Boolean(mint) &&
    !failedBuyRecoveryInFlightMints.has(mint!) &&
    !sourceSwap?.skipDirectBuyRoutes &&
    (sourceSwap?.buyRecoveryAttempt ?? 0) < executionRescueRetries
  );
}

function isTerminalBuyFailure(message: string): boolean {
  return classifyExecutionError(message).recoverable === false;
}

function scheduleFailedBuyRecovery(
  sourceSwap: DetectedSwap,
  kp: Keypair,
  reason: string
): void {
  failedBuyRecoveryInFlightMints.add(sourceSwap.outputMint);
  const decision = decideRetryAction(reason);
  const useDerivedPumpFunRescue =
    decision.program !== "pump_amm" &&
    decision.action === "rebuild-derived-accounts" &&
    !sourceSwap.forceDerivedPumpFunBuy;
  const recoverySwap: DetectedSwap = {
    ...sourceSwap,
    skipDirectBuyRoutes: false,
    forceDerivedPumpFunBuy: useDerivedPumpFunRescue,
    buyRecoveryAttempt: (sourceSwap.buyRecoveryAttempt ?? 0) + 1,
    buyRecoveryReason: reason,
  };

  console.warn(
    useDerivedPumpFunRescue
      ? `Scheduling derived-account pump.fun rescue buy for ${sourceSwap.outputMint.slice(0, 8)}... after pump.fun account-layout failure.`
      : `Scheduling direct ${decision.action} rescue buy for ${sourceSwap.outputMint.slice(0, 8)}... after direct buy failed.`
  );
  setTimeout(() => {
    if (stopRequested || !failedBuyRecoveryInFlightMints.has(sourceSwap.outputMint)) return;
    rustHotSniperPrimaryFallbackSources.add(recoverySwap.signature);
    processSwap(recoverySwap, kp).catch((err) => {
      const msg = err instanceof Error ? err.message : String(err);
      const decision = decideRetryAction(msg);
      console.error(`Direct rescue buy failed for ${sourceSwap.outputMint.slice(0, 8)}...: ${msg}`);
      if (decision.shouldQuarantine) {
        quarantinedBuyMints.set(sourceSwap.outputMint, `direct rescue ${decision.action}: ${msg}`);
      } else if (isPumpFunAccountLayoutFailure(msg)) {
        boughtMintsThisSession.delete(sourceSwap.outputMint);
        pendingBuyRetryByMint.delete(sourceSwap.outputMint);
        quarantinedBuyMints.set(sourceSwap.outputMint, `direct rescue ${decision.action}: ${msg}`);
        console.warn(
          `Buy quarantine set for ${sourceSwap.outputMint.slice(0, 8)}... after pump.fun account-layout ` +
            "rescue also failed. No more retries for this mint this session."
        );
      } else {
        boughtMintsThisSession.delete(sourceSwap.outputMint);
        pendingBuyRetryByMint.delete(sourceSwap.outputMint);
        console.warn(
          `Direct rescue failure for ${sourceSwap.outputMint.slice(0, 8)}... is recoverable ` +
            `(${decision.action}); not quarantining this mint.`
        );
      }
    }).finally(() => {
      rustHotSniperPrimaryFallbackSources.delete(recoverySwap.signature);
      failedBuyRecoveryInFlightMints.delete(sourceSwap.outputMint);
    });
  }, executionRescueDelayMs);
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
    const costBasisLamports = result.costBasisLamports ?? result.input.amountRaw;
    const pos = recordBuy(result.output.meta, result.output.amountRaw, costBasisLamports);
    const tokenMint = result.output.meta.mint;
    if (result.confirmationPending && !result.isSimulated) {
      markFastBuyConfirmationPending(tokenMint, result.signature);
    }
    boughtMintsThisSession.add(tokenMint);
    sourceBuySignatureByMint.set(tokenMint, swap.signature);
    if (!result.isSimulated) mirrorBuySignatureByMint.set(tokenMint, result.signature);
    const sourceQuote = quoteAmountDisplay(swap.amountRaw, swap.inputMint);
    const mirroredQuote = quoteAmountDisplay(result.input.amountRaw, result.input.meta.mint);
    const sourceTokenDisplay =
      swap.counterpartyAmountRaw != null
        ? `${formatTokenAmount(swap.counterpartyAmountRaw, result.output.meta.decimals)} $${result.output.meta.symbol}`
        : undefined;
    const clamped = swap.amountRaw !== result.input.amountRaw;
    const entryPrice = tokenPriceInSol(
      costBasisLamports,
      result.output.amountRaw,
      result.output.meta.decimals
    );
    const impactStr = result.priceImpactPct
      ? ` · impact ${(parseFloat(result.priceImpactPct) * 100).toFixed(2)}%`
      : "";
    console.log(`✅ Buy${simTag} (${walletTag}): ${mirroredQuote}${clamped ? ` (capped from ${sourceQuote})` : ""}`);
    console.log(`🪙 Tokens: ${outputDisplay}`);
    console.log(`💹 Entry: ${formatPrice(entryPrice)} SOL per $${pos.symbol}${impactStr}`);
    console.log(`🔗 CA: ${tokenMint}`);
    if (mcLine) console.log(mcLine);
    if (result.confirmationPending) {
      console.log("Status: broadcast accepted, awaiting on-chain confirmation (fast mode optimistic record)");
    }
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
      solAmount: result.input.meta.mint === USDC_MINT ? `${mirroredQuote} (~${formatSolAmount(costBasisLamports)} SOL)` : mirroredQuote,
      sourceAmount: sourceTokenDisplay,
      sourceSolAmount: sourceQuote,
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
    const before = getPosition(result.input.meta.mint);
    const fill = sellFillAccounting(before, result.input.amountRaw, result.output.amountRaw);
    const pos = recordSell(result.input.meta, result.input.amountRaw, result.output.amountRaw);
    cleanupHybridExitStateIfClosed(pos);
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
    console.log(`Fill PnL on $${pos.symbol}: ${formatPnl(fill.pnlLamports)}`);
    console.log(
      `Position PnL on $${pos.symbol}: ${formatPnl(pos.realizedPnlLamports)} (held: ${formatTokenAmount(Math.max(pos.heldRaw, 0), pos.decimals)})`
    );
    if (fill.uncostedRaw > 0) {
      console.warn(
        `PnL warning: sell included ${formatTokenAmount(fill.uncostedRaw, pos.decimals)} uncosted $${pos.symbol}; ` +
          "fill PnL only attributes tracked inventory."
      );
    }
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
      pnl: formatPnl(fill.pnlLamports),
      positionPnl: formatPnl(pos.realizedPnlLamports),
      txSignature: result.isSimulated ? undefined : result.signature,
    };
    void telegram.notifyTrade(notification);

    return;
  }

  console.log(`🔄 Swap${simTag} (${walletTag}): ${inputDisplay} → ${outputDisplay}`);
  if (!result.isSimulated) console.log(`📝 Tx: ${result.signature}`);
  console.log("");
}

function shortWallet(wallet: string): string {
  return `${wallet.slice(0, 4)}...${wallet.slice(-4)}`;
}

function uniqueWallets(wallets: string[]): string[] {
  return Array.from(new Set(wallets.map((wallet) => wallet.trim()).filter(Boolean)));
}

function getWatchedWallets(): string[] {
  const base = exitSignalSellsEnabled
    ? [...trackedWallets, ...exitSignalWallets]
    : [...trackedWallets];
  if (clusterTriggerEnabled && clusterConfirmWallet) base.push(clusterConfirmWallet);
  return uniqueWallets(base);
}

function isExitSignalWallet(wallet: string): boolean {
  return exitSignalSellsEnabled && exitSignalWallets.includes(wallet);
}

// --- Cluster trigger strategy state ---
const clusterWalletSet = new Set(clusterWallets);

/** Per-mint confirmation state for cluster-entry positions. */
type ClusterState = { gugConfirmed: boolean; gugDeadlineAt: number };
const clusterPositionState = new Map<string, ClusterState>();

function isClusterWallet(wallet: string): boolean {
  return clusterTriggerEnabled && clusterWalletSet.has(wallet);
}

function isClusterConfirmWallet(wallet: string): boolean {
  return clusterTriggerEnabled && clusterConfirmWallet !== "" && wallet === clusterConfirmWallet;
}

/** Record that a cluster front-runner buy passed the size gate, opening the GUg confirmation window. */
function markClusterEntry(mint: string): void {
  if (clusterPositionState.has(mint)) return;
  clusterPositionState.set(mint, {
    gugConfirmed: false,
    gugDeadlineAt: Date.now() + clusterConfirmWindowMs,
  });
}

/** Handle a buy from the confirmation-only wallet: mark the matching position as confirmed. */
function handleClusterConfirm(swap: DetectedSwap): void {
  if (!isBuySignal(swap)) return; // only buys confirm a runner
  const mint = swap.outputMint;
  const state = clusterPositionState.get(mint);
  if (!state) return; // confirmation for a mint we didn't enter via the cluster — ignore
  if (!state.gugConfirmed) {
    state.gugConfirmed = true;
    console.log(
      `✅ Cluster confirm: ${shortWallet(swap.wallet)} bought ${mint.slice(0, 8)}... — keeping normal trailing.`
    );
  }
}

/**
 * Trailing steps to use for a mint right now. Normally the global TRAILING_STEPS. For a cluster-entry
 * position whose confirmation window has elapsed without GUg appearing, switch to the tighter steps.
 */
function activeTrailingSteps(mint: string): TrailingStep[] {
  const base = trailingEnabled ? trailingSteps : [];
  if (!clusterTriggerEnabled) return base;
  const state = clusterPositionState.get(mint);
  if (!state || state.gugConfirmed) return base;
  if (Date.now() >= state.gugDeadlineAt && clusterNoConfirmTrailingSteps.length > 0) {
    return clusterNoConfirmTrailingSteps;
  }
  return base;
}

/** Drop cluster state for mints with no open position once their window has long expired. */
function pruneClusterState(): void {
  if (clusterPositionState.size === 0) return;
  const now = Date.now();
  for (const [mint, state] of clusterPositionState) {
    if (now < state.gugDeadlineAt + 60_000) continue;
    const pos = getPosition(mint);
    if (!pos || pos.heldRaw <= 0) clusterPositionState.delete(mint);
  }
}

async function closePositionFromExitSignal(swap: DetectedSwap): Promise<void> {
  const mint = swap.inputMint;
  const position = getPosition(mint);
  const walletTag = shortWallet(swap.wallet);
  if (!position || position.heldRaw <= 0) {
    console.log(
      `Exit signal from ${walletTag} for ${mint.slice(0, 8)}... ignored: no matching open position.`
    );
    recordMirrorSkip(swap, "exit signal no open position");
    return;
  }

  if (exitSignalSellsInFlight.has(mint) || riskSellsInFlight.has(mint) || fastSellConfirmPendingByMint.has(mint)) {
    console.log(`Exit signal for $${position.symbol} skipped: sell already in flight.`);
    return;
  }

  exitSignalSellsInFlight.add(mint);
  const note = `exit signal from ${walletTag}`;
  try {
    console.log(
      `Exit signal sell detected from ${walletTag}: closing ${formatTokenAmount(position.heldRaw, position.decimals)} $${position.symbol}.`
    );
    if (simulationMode) {
      await sellSimulatedPosition(position, note);
      return;
    }

    await sellLivePosition(position, note, undefined, false);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`Exit signal sell failed for $${position.symbol}: ${msg}`);
    void telegram.notifyError(`Exit signal sell failed for $${position.symbol}`, msg);
  } finally {
    exitSignalSellsInFlight.delete(mint);
  }
}

async function processSwap(swap: DetectedSwap, kp: Keypair): Promise<void> {
  if (stopRequested) return;
  noteTrackedSourceSignal(swap);
  const ts = Date.now();
  const processStartedAt = Date.now();
  const elapsed = () => `${Date.now() - processStartedAt}ms`;
  const sourceLatencyDetails = {
    detector: swap.detector ?? "unknown",
    providerCreatedAtMs: swap.providerCreatedAtMs,
    receivedAtMs: swap.receivedAtMs,
    detectedAtMs: swap.detectedAtMs,
    processStartedAtMs: processStartedAt,
    providerToReceivedMs:
      swap.providerCreatedAtMs != null && swap.receivedAtMs != null
        ? swap.receivedAtMs - swap.providerCreatedAtMs
        : undefined,
    receivedToDetectedMs:
      swap.receivedAtMs != null && swap.detectedAtMs != null
        ? swap.detectedAtMs - swap.receivedAtMs
        : undefined,
    detectedToProcessMs:
      swap.detectedAtMs != null ? processStartedAt - swap.detectedAtMs : undefined,
    providerToProcessMs:
      swap.providerCreatedAtMs != null ? processStartedAt - swap.providerCreatedAtMs : undefined,
    sourceSlot: swap.sourceSlot,
    sourceTransactionIndex: swap.sourceTransactionIndex,
  };
  const isTrackedSourceWallet = trackedWallets.includes(swap.wallet);
  const isConfirmOnlyWallet = !isTrackedSourceWallet && isClusterConfirmWallet(swap.wallet);
  const isExitOnlyWallet =
    !isTrackedSourceWallet && !isConfirmOnlyWallet && isExitSignalWallet(swap.wallet);
  if (!isTrackedSourceWallet && !isConfirmOnlyWallet && !isExitOnlyWallet) {
    console.warn(
      `Ignoring swap from untracked wallet ${swap.wallet.slice(0, 4)}...${swap.wallet.slice(-4)} ` +
        `(tracked: ${trackedWallets.map(shortWallet).join(", ")}).`
    );
    recordMirrorSkip(swap, "untracked wallet");
    return;
  }
  if (isConfirmOnlyWallet) {
    handleClusterConfirm(swap);
    return;
  }
  if (isExitOnlyWallet) {
    if (!isSellSignal(swap)) {
      console.log(`Exit signal wallet ${shortWallet(swap.wallet)} buy ignored; exit wallets only trigger sells.`);
      return;
    }
    await closePositionFromExitSignal(swap);
    return;
  }
  let lockedBuyMint: string | null = null;
  let lockedOpenPositionMint: string | null = null;
  let lockedFastSellMint: string | null = null;
  let fastSellLockShouldPersist = false;
  let retryPendingMint: string | null = null;
  let persistentBuyLockMint: string | null = null;
  let persistentBuyLockOwner: string | undefined;
  let persistentBuyLockCommitted = false;
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
    `⚡ Processing ${isBuySignal(swap) ? "buy" : "sell"} from ${swap.wallet.slice(0, 4)}…${swap.wallet.slice(-4)} (${swap.signature.slice(0, 8)}…)`
  );

  recordExecutionTelemetrySync({
    kind: "source-detected",
    side: isBuySignal(swap) ? "buy" : isSellSignal(swap) ? "sell" : "swap",
    mint: isBuySignal(swap) ? swap.outputMint : swap.inputMint,
    sourceSignature: swap.signature,
    wallet: swap.wallet,
    detector: swap.detector,
    elapsedMs: sourceLatencyDetails.providerToProcessMs,
    success: true,
    details: sourceLatencyDetails,
  });

  const executionPlan = planExecution(swap);
  const useBagsDirectLane = shouldUseBagsDirectLane(executionPlan);
  const decisionAtMs = Date.now();
  console.log(
    `Execution plan: ${executionPlan.route} (${executionPlan.reason}) for ${executionPlan.mint.slice(0, 8)}...`
  );
  recordExecutionTelemetrySync({
    kind: "execution-plan",
    route: executionPlan.route,
    side: executionPlan.side,
    mint: executionPlan.mint,
    sourceSignature: swap.signature,
    wallet: swap.wallet,
    detector: swap.detector,
    elapsedMs: decisionAtMs - processStartedAt,
    success: true,
    details: {
      reason: executionPlan.reason,
      processToDecisionMs: decisionAtMs - processStartedAt,
      ...sourceLatencyDetails,
    },
  });

  if (isBuySignal(swap)) {
    const sourceTokenRaw = sourceBuyTokenAmountRaw(swap);
    if (sourceTokenRaw > 0) {
      recordSourceBuyRaw(swap.wallet, swap.outputMint, sourceTokenRaw);
    }
  }

  if (isBuySignal(swap) && buysPaused) {
    console.log(
      `⏸️ Buys paused: skipping buy of ${swap.outputMint.slice(0, 8)}... ` +
        "(open positions still managed; use Resume to re-enable entries)."
    );
    recordMirrorSkip(swap, "buys paused");
    return;
  }

  let proportionalSellAmountRaw: number | undefined;
  let confirmedSellAmountRaw: number | undefined;
  const queuedSellCapRaw = isSellSignal(swap)
    ? queuedSellCapBySignature.get(swap.signature)
    : undefined;
  if (isSellSignal(swap)) {
    const mint = swap.inputMint;
    if (!simulationMode && hasFastBuyConfirmationPending(mint)) {
      queueSourceSell(swap);
      console.log(
        `Queued sell: ${mint.slice(0, 8)}... waiting for pending buy confirmation.`
      );
      recordMirrorSkip(swap, "sell queued behind pending buy confirmation");
      return;
    }
    if (
      buyMintsInFlight.has(mint) ||
      pendingBuyRetryByMint.has(mint) ||
      failedBuyRecoveryInFlightMints.has(mint)
    ) {
      pendingBuyRetryByMint.delete(mint);
      failedBuyRecoveryInFlightMints.delete(mint);
      fastBuyConfirmRetryScheduledByMint.delete(mint);
      quarantinedBuyMints.set(mint, SOURCE_SOLD_WHILE_BUY_PENDING_REASON);
      console.warn(
        `Canceled pending buy for ${mint.slice(0, 8)}... because the source wallet already sold it.`
      );
    }

    const sourceSellRaw = sourceSellTokenAmountRaw(swap);
    if (
      hybridExitEnabled &&
      hybridExitSourceExitRemaining &&
      sourceSellRaw > 0 &&
      (getPosition(mint)?.heldRaw ?? 0) > 0
    ) {
      console.log(
        `Hybrid source exit: source sold ${mint.slice(0, 8)}...; closing remaining bot position.`
      );
    } else if (mirrorSellMode === "proportional" && sourceSellRaw > 0) {
      const position = getPosition(mint);
      if (position && position.heldRaw > 0) {
        const proportional = calculateProportionalSellRaw({
          wallet: swap.wallet,
          mint,
          sourceSellAmountRaw: sourceSellRaw,
          ourHeldRaw: position.heldRaw,
          minPct: mirrorSellProportionalMinPct,
          maxPct: mirrorSellProportionalMaxPct,
        });
        if (!proportional.fallback && proportional.amountRaw > 0) {
          proportionalSellAmountRaw = proportional.amountRaw;
          console.log(
            `Proportional mirror sell: source sold ${proportional.fractionPct.toFixed(1)}% ` +
              `(${sourceSellRaw}/${proportional.sourceHeldRaw} raw); selling ` +
              `${formatTokenAmount(proportionalSellAmountRaw, position.decimals)} $${position.symbol}.`
          );
        } else if (mirrorSellProportionalFallback === "skip") {
          console.log(
            `Proportional mirror sell skipped for ${mint.slice(0, 8)}...: source position history unavailable.`
          );
          recordSourceSellRaw(swap.wallet, mint, sourceSellRaw);
          recordMirrorSkip(swap, "proportional mirror sell unavailable");
          return;
        } else {
          console.log(
            `Proportional mirror sell fallback for ${mint.slice(0, 8)}...: source position history unavailable; closing full bot position.`
          );
        }
      }
    }
    if (sourceSellRaw > 0) {
      recordSourceSellRaw(swap.wallet, mint, sourceSellRaw);
    }
  }

  const mirrorRemainderIntent: PostSellRemainderIntent | null =
    isSellSignal(swap) && proportionalSellAmountRaw == null
      ? {
          kind: "source-full",
          sourceSellVersion: sourceSignalStateByMint.get(swap.inputMint)?.version ?? 0,
        }
      : null;

  // Skip mirroring source sells if mirror-sells is OFF. We still log it so user has a paper trail.
  if (!mirrorSellsEnabled && isSellSignal(swap)) {
    console.log(
      `ℹ️  Source ${swap.wallet.slice(0, 4)}…${swap.wallet.slice(-4)} sold ${swap.inputMint.slice(0, 6)}… — not mirroring (mirror-sells OFF). Press 'm' to enable, or 'e'/'a' to exit manually.`
    );
    return;
  }

  // Circuit breaker gate: only blocks BUYS, never sells. Existing positions can always exit.
  if (isBuySignal(swap)) {
    const blocked = shouldBlockNewBuys();
    if (blocked) {
      console.log(`⛔ Skipping buy: ${blocked}.`);
      return;
    }
  }

  // Cluster size gate: only mirror a front-runner wallet when its single source buy is
  // large enough. In-memory check, no execution-path latency. On pass, open the GUg confirmation window.
  if (isBuySignal(swap) && isClusterWallet(swap.wallet)) {
    const sourceLamports = swap.isNativeInput ? swap.amountRaw : 0;
    if (sourceLamports < clusterMinSourceBuyLamports) {
      console.log(
        `Skipping buy: ${shortWallet(swap.wallet)} source buy ${formatSolAmount(sourceLamports)} SOL ` +
          `< ${formatSolAmount(clusterMinSourceBuyLamports)} SOL cluster minimum.`
      );
      return;
    }
    markClusterEntry(swap.outputMint);
  }

  if (skipSourcePreexistingMints && isBuySignal(swap)) {
    const mint = swap.outputMint;
    if (sourcePreexistingBuyMints.has(mint)) {
      console.log(
        `Skipping buy: ${mint.slice(0, 8)}... was already held/bought by source before startup.`
      );
      return;
    }
  }

  // Skip duplicate buys of the same token if buy-once is ON. Position lookup is in-memory (no API cost).
  if (isBuySignal(swap)) {
    const mint = swap.outputMint;
    const quarantineReason = quarantinedBuyMints.get(mint);
    if (quarantineReason) {
      if (isSourceSoldWhileBuyPendingQuarantine(quarantineReason)) {
        quarantinedBuyMints.delete(mint);
        console.log(
          `Clearing stale source-sold quarantine for ${mint.slice(0, 8)}... after fresh source buy signal.`
        );
      } else {
        console.log(
          `Skipping buy: ${mint.slice(0, 8)}... quarantined this session after failed on-chain buy (${quarantineReason}).`
        );
        return;
      }
    }
    if (
      !swap.skipDirectBuyRoutes &&
      failedBuyRecoveryInFlightMints.has(mint) &&
      (swap.buyRecoveryAttempt ?? 0) === 0
    ) {
      console.log(
        `Skipping buy: ${mint.slice(0, 8)}... failed direct buy is already waiting on one direct rescue.`
      );
      return;
    }
    const existing = getPosition(mint);
    if (
      buyOncePerTokenEnabled &&
      (liveBuyAttemptedMintsThisSession.has(mint) ||
        boughtMintsThisSession.has(mint) ||
        (existing && existing.buysCount > 0))
    ) {
      console.log(
        `Skipping buy: already bought ${mint.slice(0, 8)}... this session (buy-once ON).`
      );
      recordMirrorSkip(swap, "buy-once already bought");
      return;
    }
    if (!buyOncePerTokenEnabled && maxBuysPerToken != null && existing && existing.buysCount >= maxBuysPerToken) {
      console.log(
        `Skipping buy: ${mint.slice(0, 8)}... max buys per token reached ` +
          `(${existing.buysCount}/${maxBuysPerToken}).`
      );
      recordMirrorSkip(swap, "max buys per token reached", {
        buysCount: existing.buysCount,
        maxBuysPerToken,
      });
      return;
    }
    if (maxSolPerTokenLamports != null) {
      const nextBuyLamports =
        mirrorQuoteMintForBuy(swap) === WRAPPED_SOL_MINT ? mirroredBuyAmountRaw(swap) : null;
      const projectedLamports = (existing?.totalSolSpentLamports ?? 0) + (nextBuyLamports ?? 0);
      if (nextBuyLamports != null && projectedLamports > maxSolPerTokenLamports) {
        console.log(
          `Skipping buy: ${mint.slice(0, 8)}... max SOL per token exposure would be exceeded ` +
            `(${formatSolAmount(existing?.totalSolSpentLamports ?? 0)} + ${formatSolAmount(nextBuyLamports)} > ` +
            `${formatSolAmount(maxSolPerTokenLamports)} SOL).`
        );
        recordMirrorSkip(swap, "max SOL per token exceeded", {
          currentLamports: existing?.totalSolSpentLamports ?? 0,
          nextBuyLamports,
          maxSolPerTokenLamports,
        });
        return;
      }
    }
    if (maxSessionDeployedLamports != null) {
      const nextBuyLamports =
        mirrorQuoteMintForBuy(swap) === WRAPPED_SOL_MINT ? mirroredBuyAmountRaw(swap) : null;
      const deployedLamports = getTotals().totalSolSpentLamports;
      const projectedLamports = deployedLamports + (nextBuyLamports ?? 0);
      if (nextBuyLamports != null && projectedLamports > maxSessionDeployedLamports) {
        console.log(
          `Skipping buy: session deployed SOL cap would be exceeded ` +
            `(${formatSolAmount(deployedLamports)} + ${formatSolAmount(nextBuyLamports)} > ` +
            `${formatSolAmount(maxSessionDeployedLamports)} SOL).`
        );
        recordMirrorSkip(swap, "max session deployed SOL exceeded", {
          deployedLamports,
          nextBuyLamports,
          maxSessionDeployedLamports,
        });
        return;
      }
    }
    if (maxOpenDeployedLamports != null) {
      const nextBuyLamports =
        mirrorQuoteMintForBuy(swap) === WRAPPED_SOL_MINT ? mirroredBuyAmountRaw(swap) : null;
      const openDeployedLamports = getTotals().openCostBasisLamports;
      const projectedOpenLamports = openDeployedLamports + (nextBuyLamports ?? 0);
      if (nextBuyLamports != null && projectedOpenLamports > maxOpenDeployedLamports) {
        console.log(
          `Skipping buy: open deployed SOL cap would be exceeded ` +
            `(${formatSolAmount(openDeployedLamports)} + ${formatSolAmount(nextBuyLamports)} > ` +
            `${formatSolAmount(maxOpenDeployedLamports)} SOL).`
        );
        recordMirrorSkip(swap, "max open deployed SOL exceeded", {
          openDeployedLamports,
          nextBuyLamports,
          maxOpenDeployedLamports,
        });
        return;
      }
    }
    if (shouldDeferToRustHotSniperPrimary(swap, executionPlan)) {
      if (
        rustHotBuyRecordedByMint.has(mint) ||
        liveBuyAttemptedMintsThisSession.has(mint) ||
        boughtMintsThisSession.has(mint) ||
        (existing && existing.buysCount > 0)
      ) {
        pendingBuyRetryByMint.delete(mint);
        console.log(
          `Skipping TypeScript buy: Rust hot sniper already bought ${mint.slice(0, 8)}... this session.`
        );
        return;
      }
      if (rustHotSniperPrimaryPendingMints.has(mint)) {
        pendingBuyRetryByMint.set(mint, swap);
        console.log(
          `Skipping TypeScript buy: Rust hot sniper primary already pending for ${mint.slice(0, 8)}... ` +
            "(queued latest fallback signal)."
        );
        return;
      }
      rustHotSniperPrimaryPendingMints.add(mint);
      pendingBuyRetryByMint.set(mint, swap);
      console.log(
        `Rust hot sniper primary: deferring TypeScript fallback for ${mint.slice(0, 8)}... ` +
          (rustHotSniperPrimaryFallbackEnabled
            ? `${rustHotSniperPrimaryFallbackDelayMs}ms.`
            : "disabled; Rust is exclusive for this mint.")
      );
      if (!rustHotSniperPrimaryFallbackEnabled) {
        return;
      }
      // The Rust hot sniper (ShredStream) gets a short head-start to land a
      // source-replay buy. ShredStream is best-effort: if its shred for the
      // source tx never arrives, the hot sniper simply never fires — that is a
      // MISS, not a failure event, so we must not wait for a "confirmed failure"
      // that will never come. After the head-start window we always back the buy
      // up through the normal pipeline, which under RUST_ONLY_BUYS still
      // broadcasts via the Rust executor command bridge (no TypeScript tx is
      // built). The shared buy-once gate in the Rust sidecar reserves the mint
      // before broadcasting on both paths, so even if the hot sniper fires late
      // only one broadcast can ever land — racing the two paths is safe.
      setTimeout(() => {
        if (!rustHotSniperPrimaryPendingMints.has(mint)) return;
        rustHotSniperPrimaryPendingMints.delete(mint);
        const fallback = pendingBuyRetryByMint.get(mint) ?? swap;
        pendingBuyRetryByMint.delete(mint);
        if (
          stopRequested ||
          rustHotBuyRecordedByMint.has(mint) ||
          liveBuyAttemptedMintsThisSession.has(mint) ||
          boughtMintsThisSession.has(mint) ||
          quarantinedBuyMints.has(mint) ||
          (getPosition(mint)?.buysCount ?? 0) > 0
        ) {
          // Hot sniper already handled this mint (or it is otherwise resolved).
          return;
        }
        rustHotSniperPrimaryFallbackSources.add(fallback.signature);
        console.log(
          `Rust hot sniper head-start elapsed for ${mint.slice(0, 8)}...; ` +
            "driving Rust executor backstop buy (shared buy-once gate prevents duplicates)."
        );
        processSwap(fallback, kp)
          .catch((err) => {
            console.error("Rust hot sniper backstop buy failed:", err);
          })
          .finally(() => {
            rustHotSniperPrimaryFallbackSources.delete(fallback.signature);
          });
      }, rustHotSniperPrimaryFallbackDelayMs);
      return;
    }
    const rustExecutorCanHandleBuy =
      rustExecutorEnabled &&
      (executionPlan.route === "pumpfun-bonding-curve" ||
        executionPlan.route === "pumpfun-v2-token2022" ||
        executionPlan.route === "pumpswap-graduated");
    if (useBagsDirectLane) {
      console.log(
        `BAGS direct lane: allowing ${mint.slice(0, 8)}... through Jupiter/Sender ` +
          "because BAGS_DIRECT_ENABLED=true; global Jupiter fallback remains unchanged."
      );
    }
    if (rustOnlyBuys && !rustExecutorCanHandleBuy && !useBagsDirectLane) {
      console.log(
        `Skipping buy: RUST_ONLY_BUYS=true and no Rust buy path accepted ` +
          `${mint.slice(0, 8)}... (${executionPlan.route}).`
      );
      recordMirrorSkip(swap, "rust-only no accepted buy path", { route: executionPlan.route });
      pendingBuyRetryByMint.delete(mint);
      releaseBuyLocks();
      return;
    } else if (rustOnlyBuys) {
      console.log(
        `Rust-only buy mode: ${mint.slice(0, 8)}... passed filters; Rust executor will broadcast ${executionPlan.route}.`
      );
    }
    if (buyMintsInFlight.has(mint)) {
      // Keep only the latest duplicate source buy so a dropped first broadcast can retry promptly.
      if (!failedBuyRecoveryInFlightMints.has(mint)) {
        pendingBuyRetryByMint.set(mint, swap);
      }
      console.log(
        failedBuyRecoveryInFlightMints.has(mint)
          ? `Skipping buy: ${mint.slice(0, 8)}... buy already in flight and rescue pending.`
          : `Skipping buy: ${mint.slice(0, 8)}... buy already in flight (queued one retry signal).`
      );
      recordMirrorSkip(swap, "buy already in flight", {
        rescuePending: failedBuyRecoveryInFlightMints.has(mint),
      });
      return;
    }
    buyMintsInFlight.add(mint);
    lockedBuyMint = mint;
    retryPendingMint = mint;
  }

  if (maxOpenPositions != null && isBuySignal(swap)) {
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
        recordMirrorSkip(swap, "max open positions reached", {
          openCount,
          pendingCount,
          maxOpenPositions,
        });
        releaseBuyLocks();
        return;
      }

      openPositionBuyMintsInFlight.add(mint);
      lockedOpenPositionMint = mint;
    }
  }

  if (prebuyInferredMcFilterEnabled && isBuySignal(swap)) {
    const filter = checkPrebuyInferredMarketCap(swap);
    if (!filter.allowed) {
      console.log(`Skipping buy: ${filter.reason}.`);
      recordMirrorSkip(swap, "prebuy inferred market-cap filter", { reason: filter.reason });
      releaseBuyLocks();
      return;
    }
    console.log(filter.reason);
  }

  if (prebuyMoralisFilterEnabled && isBuySignal(swap)) {
    const filter = await checkPrebuyMoralisFilter(swap.outputMint);
    if (!filter.allowed) {
      console.log(`Skipping buy: ${filter.reason}.`);
      recordMirrorSkip(swap, "prebuy Moralis filter", { reason: filter.reason });
      releaseBuyLocks();
      return;
    }
    console.log(filter.reason);
  }

  if (blockPumpfunMayhem && isBuySignal(swap)) {
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

  if (isBuySignal(swap) && simulationMode) {
    const quoteMint = mirrorQuoteMintForBuy(swap);
    const mirroredAmount = mirroredBuyAmountRaw(swap);
    if (mirroredAmount == null) {
      console.log(
        `Skipping buy: copy-sized amount for ${swap.outputMint.slice(0, 8)}... is below configured minimum ` +
          `(${copyBuyPercentage}% of ${quoteAmountDisplay(swap.amountRaw, quoteMint)}).`
      );
      releaseBuyLocks();
      return;
    }
    const spendLamports = quoteMint === WRAPPED_SOL_MINT ? mirroredAmount : 0;
    const shadow = shadowCanSpend(spendLamports);
    if (!shadow.ok) {
      console.log(`Shadow reserve check failed:`);
      console.log(`   Available: ${formatSolAmount(shadow.available)} SOL`);
      console.log(`   Required incl. shadow fee: ${formatSolAmount(shadow.required)} SOL`);
      console.log(`   Source trade: ${quoteAmountDisplay(swap.amountRaw, swap.inputMint)}`);
      releaseBuyLocks();
      return;
    }
    console.log(
      `Shadow reserve check OK: available ${formatSolAmount(shadow.available)} SOL, ` +
        `required ${formatSolAmount(shadow.required)} SOL incl. estimated fees.`
    );
  }

  // Fast SOL reserve check - only for buys and only in LIVE mode (sells don't spend SOL)
  if (isBuySignal(swap) && !simulationMode) {
    const quoteMint = mirrorQuoteMintForBuy(swap);
    const mirroredAmount = mirroredBuyAmountRaw(swap);
    if (mirroredAmount == null) {
      console.log(
        `Skipping buy: copy-sized amount for ${swap.outputMint.slice(0, 8)}... is below configured minimum ` +
          `(${copyBuyPercentage}% of ${quoteAmountDisplay(swap.amountRaw, quoteMint)}).`
      );
      releaseBuyLocks();
      return;
    }
    // Guard against a malformed source amount (e.g. a re-fed backstop swap with a
    // missing amountRaw) producing NaN downstream in the gas-reserve math.
    if (!Number.isFinite(mirroredAmount) || mirroredAmount <= 0) {
      console.log(
        `Skipping buy: invalid copy-sized amount for ${swap.outputMint.slice(0, 8)}... ` +
          `(source amountRaw=${swap.amountRaw}).`
      );
      recordMirrorSkip(swap, "invalid copy-sized amount");
      releaseBuyLocks();
      return;
    }
    let currentBalance = getCachedBalance() ?? lastBalanceLamports ?? 0;
    if (currentBalance <= 0) {
      currentBalance = await updateBalanceCache(readConnection, kp.publicKey);
    } else if ((getCachedBalanceAgeMs() ?? 0) > 30_000) {
      void updateBalanceCache(readConnection, kp.publicKey, true);
    }
    
    // Check if we have enough SOL after reserving gas fees
    if (quoteMint === WRAPPED_SOL_MINT && !hasEnoughSOLForTrade(mirroredAmount, currentBalance)) {
      const balanceInfo = getBalanceInfo(currentBalance);
      console.log(`⛽ Insufficient SOL (reserve check):`);
      console.log(`   Total: ${balanceInfo.total} SOL`);
      console.log(`   Available: ${balanceInfo.available} SOL`);
      console.log(`   Reserved: ${balanceInfo.reserved} SOL`);
      console.log(`   Required: ${(mirroredAmount / LAMPORTS_PER_SOL).toFixed(4)} SOL`);
      if (mirroredAmount !== swap.amountRaw) {
        console.log(`   Source trade: ${quoteAmountDisplay(swap.amountRaw, swap.inputMint)}`);
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

    if (quoteMint === USDC_MINT) {
      const usdcBalance = (await getTokenBalanceFast(kp.publicKey, USDC_MINT)) ?? (await getTokenBalance(kp.publicKey, USDC_MINT));
      if (usdcBalance < mirroredAmount) {
        console.log(`Insufficient USDC:`);
        console.log(`   Available: ${quoteAmountDisplay(usdcBalance, USDC_MINT)}`);
        console.log(`   Required: ${quoteAmountDisplay(mirroredAmount, USDC_MINT)}`);
        console.log(`   Source trade: ${quoteAmountDisplay(swap.amountRaw, swap.inputMint)}`);
        void telegram.notifyError(
          `Trade skipped: Insufficient USDC`,
          `Available: ${quoteAmountDisplay(usdcBalance, USDC_MINT)} | Required: ${quoteAmountDisplay(mirroredAmount, USDC_MINT)}`
        );
        releaseBuyLocks();
        return;
      }
      if (!hasEnoughSOLForTrade(0, currentBalance)) {
        const balanceInfo = getBalanceInfo(currentBalance);
        console.log(`Insufficient SOL reserve for USDC trade fees: available ${balanceInfo.available} SOL, reserve ${balanceInfo.reserved} SOL`);
        releaseBuyLocks();
        return;
      }
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
  if (false && simulationMode && isBuySignal(swap)) {
    console.log(`🎭 Simulation Mode: SOL reserve check disabled (no real gas fees)`);
  }
  
  try {
    if (isSellSignal(swap) && !simulationMode) {
      const sellMint = swap.inputMint;
      const currentPosition = getPosition(sellMint);
      if (
        currentPosition &&
        mirrorBuySignatureByMint.has(sellMint) &&
        currentPosition.sellsCount === 0 &&
        Date.now() - currentPosition.firstSeenAt < 120_000
      ) {
        await reconcileOpenPositionWithConfirmedBuy(currentPosition);
      }
      if (fastSellConfirmPendingByMint.has(sellMint)) {
        queueSourceSell(swap);
        console.log(
          `Queued sell: ${sellMint.slice(0, 8)}... another sell is awaiting confirmation.`
        );
        recordMirrorSkip(swap, "sell queued behind pending confirmation");
        return;
      }
      fastSellConfirmPendingByMint.add(sellMint);
      lockedFastSellMint = sellMint;
      let confirmedBalance: number;
      try {
        confirmedBalance = await refreshConfirmedInventory(sellMint);
      } catch (err) {
        queueSourceSell(swap, 500);
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(
          `Confirmed sell inventory unavailable for ${sellMint.slice(0, 8)}...; ` +
            `sell retained for retry (${msg}).`
        );
        return;
      }
      if (confirmedBalance <= 0) {
        console.log(
          `Skipping sell: confirmed wallet inventory is 0 for ${sellMint.slice(0, 8)}...`
        );
        recordMirrorSkip(swap, "confirmed wallet inventory is zero");
        return;
      }
      confirmedSellAmountRaw =
        proportionalSellAmountRaw == null
          ? cappedQueuedSellRaw(confirmedBalance, queuedSellCapRaw ?? confirmedBalance)
          : Math.min(proportionalSellAmountRaw, confirmedBalance);
      if (confirmedSellAmountRaw <= 0) {
        console.log(
          `Skipping queued sell: no pre-reentry inventory remains for ${sellMint.slice(0, 8)}...`
        );
        return;
      }
    }

    if (isBuySignal(swap) && buyOncePerTokenEnabled && !simulationMode) {
      const lock = acquirePersistentBuyLock(swap.outputMint, swap.signature);
      if (!lock.ok) {
        console.log(
          `Skipping buy: ${swap.outputMint.slice(0, 8)}... already locked by another live buy attempt (${lock.reason}).`
        );
        return;
      }
      persistentBuyLockOwner = lock.owner;
      persistentBuyLockMint = swap.outputMint;
      liveBuyAttemptedMintsThisSession.add(swap.outputMint);
    }

    const result = await mirrorSwap(useBagsDirectLane ? { ...swap, skipDirectBuyRoutes: true } : swap, kp, {
      persistentBuyLockOwner,
      exactAmountRaw: confirmedSellAmountRaw,
      allowBagsDirectJupiter: useBagsDirectLane,
      onFastBuyConfirmFailure: ({ mint, tokenAmountRaw, reason, sourceSwap }) => {
        if (rustHotBuyRecordedByMint.has(mint)) {
          console.warn(
            `Ignoring TypeScript buy confirmation failure for ${mint.slice(0, 8)}... ` +
              "because Rust hot sniper already recorded this mint."
          );
          return;
        }
        unrecordZeroSell(mint, tokenAmountRaw);
        mirrorBuySignatureByMint.delete(mint);
        clearFastBuyConfirmationPending(mint);
        confirmedFastBuyTokensByMint.delete(mint);
        if (shouldHoldBuyOnceAfterFailure({ buyOncePerTokenEnabled, reason })) {
          holdBuyOnceMintAfterBroadcastUncertainty(mint, reason);
          return;
        }
        if (shouldAttemptFailedBuyRecovery(sourceSwap, reason)) {
          boughtMintsThisSession.delete(mint);
          liveBuyAttemptedMintsThisSession.delete(mint);
          pendingBuyRetryByMint.delete(mint);
          fastBuyConfirmRetryScheduledByMint.delete(mint);
          scheduleFailedBuyRecovery(sourceSwap, kp, reason);
          return;
        }
        if (isPumpFunAccountLayoutFailure(reason)) {
          boughtMintsThisSession.delete(mint);
          liveBuyAttemptedMintsThisSession.delete(mint);
          pendingBuyRetryByMint.delete(mint);
          fastBuyConfirmRetryScheduledByMint.delete(mint);
          failedBuyRecoveryInFlightMints.delete(mint);
          quarantinedBuyMints.set(mint, reason);
          console.warn(
            `Buy failed for ${mint.slice(0, 8)}... with pump.fun account-layout error after rescue; ` +
              "quarantining this mint for the rest of the session."
          );
          return;
        }
        const decision = decideRetryAction(reason);
        pendingBuyRetryByMint.delete(mint);
        fastBuyConfirmRetryScheduledByMint.delete(mint);
        if (decision.shouldQuarantine) {
          quarantinedBuyMints.set(mint, reason);
          console.warn(
            `Buy quarantine set for ${mint.slice(0, 8)}... after terminal ${decision.category} failure (${reason}). ` +
              "No more retries for this mint this session."
          );
        } else {
          boughtMintsThisSession.delete(mint);
          liveBuyAttemptedMintsThisSession.delete(mint);
          failedBuyRecoveryInFlightMints.delete(mint);
          console.warn(
            `Buy failed for ${mint.slice(0, 8)}... with recoverable ${decision.category} failure; ` +
              `retry action=${decision.action}. Not quarantining this mint.`
          );
        }
        return;
        if (boughtMintsThisSession.delete(mint)) {
          console.warn(
            `🧹 Buy-once marker cleared for ${mint.slice(0, 8)}… after failed fast-mode buy.`
          );
        }
        if (shouldQuarantineFailedBuy(reason)) {
          quarantinedBuyMints.set(mint, reason);
          pendingBuyRetryByMint.delete(mint);
          console.warn(
            `🚧 Buy quarantine set for ${mint.slice(0, 8)}… after failed on-chain buy (${reason}). ` +
              "No more retries for this mint this session."
          );
          return;
        }
        if (sourceSwap) {
          pendingBuyRetryByMint.set(mint, sourceSwap!);
          if (!fastBuyConfirmRetryScheduledByMint.has(mint)) {
            fastBuyConfirmRetryScheduledByMint.add(mint);
            setTimeout(() => {
              if (stopRequested) {
                fastBuyConfirmRetryScheduledByMint.delete(mint);
                return;
              }
              const pending = pendingBuyRetryByMint.get(mint);
              const existing = getPosition(mint);
              if (
                !pending ||
                boughtMintsThisSession.has(mint) ||
                (existing && existing.buysCount > 0) ||
                quarantinedBuyMints.has(mint)
              ) {
                fastBuyConfirmRetryScheduledByMint.delete(mint);
                return;
              }
              pendingBuyRetryByMint.delete(mint);
              processSwap(pending!, kp)
                .catch((err) => {
                  console.error("Fast-confirm buy retry failed:", err);
                })
                .finally(() => {
                  fastBuyConfirmRetryScheduledByMint.delete(mint);
                });
            }, 0);
          }
        }
      },
      onFastBuyConfirmSuccess: ({ mint, tokenAmountRaw, solLamports, signature, sourceSwap }) => {
        if (rustHotBuyRecordedByMint.has(mint) && mirrorBuySignatureByMint.get(mint) !== signature) {
          console.warn(
            `Ignoring late TypeScript buy confirmation ${signature.slice(0, 8)}... for ${mint.slice(0, 8)}... ` +
              "because Rust hot sniper already recorded this mint."
          );
          return;
        }
        fastBuyConfirmRetryScheduledByMint.delete(mint);
        failedBuyRecoveryInFlightMints.delete(mint);
        pendingBuyRetryByMint.delete(mint);
        mirrorBuySignatureByMint.set(mint, signature);
        reconcileFastBuyTokens({
          mint,
          expectedTokenAmountRaw: tokenAmountRaw,
          expectedSolLamports: solLamports,
          quoteMint: sourceSwap?.inputMint,
          signature,
        })
          .catch((err) => {
            const msg = err instanceof Error ? err.message : String(err);
            console.warn(`Fast mirrored buy reconciliation failed for ${mint.slice(0, 8)}…: ${msg}`);
          })
          .finally(() => {
            clearFastBuyConfirmationPendingSoon(mint, signature);
          });
      },
      onFastSellConfirmSuccess: ({ mint, tokenAmountRaw, solLamports, signature }) => {
        reconcileFastSellExecution({
          mint,
          expectedTokenAmountRaw: tokenAmountRaw,
          expectedSolLamports: solLamports,
          signature,
        }).catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          console.warn(`Fast mirrored sell reconciliation failed for ${mint.slice(0, 8)}…: ${msg}`);
        }).finally(() => {
          if (mirrorRemainderIntent) {
            schedulePostSellRemainderCheck(mint, mirrorRemainderIntent);
          } else {
            clearFastSellConfirmationPendingSoon(mint);
          }
        });
      },
      onFastSellConfirmFailure: ({ mint, tokenAmountRaw, solLamports, reason }) => {
        fastSellConfirmPendingByMint.delete(mint);
        const rolledBack = unrecordSell(mint, tokenAmountRaw, solLamports);
        if (rolledBack) {
          console.warn(
            `🧹 Phantom mirrored sell cleaned up: ${mint.slice(0, 8)}… ` +
              `(reason: ${reason}). Sell removed from tracking.`
          );
        }
        if (queuedSourceSellByMint.has(mint)) {
          expandQueuedSellAfterFailure(mint);
          drainQueuedSourceSell(mint);
          return;
        }
        const retryPosition = getPosition(mint);
        if (
          retryPosition &&
          retryPosition.heldRaw > 0 &&
          keypair &&
          !stopRequested &&
          !fastSellConfirmRetryScheduledByMint.has(mint)
        ) {
          fastSellConfirmRetryScheduledByMint.add(mint);
          setTimeout(() => {
            const position = getPosition(mint);
            if (!position || position.heldRaw <= 0 || !keypair || stopRequested) {
              fastSellConfirmRetryScheduledByMint.delete(mint);
              return;
            }
            sellLivePosition(position, "retry after failed mirror sell", undefined, false)
              .catch((err) => {
                const msg = err instanceof Error ? err.message : String(err);
                console.warn(`Fast mirrored sell retry failed for ${mint.slice(0, 8)}…: ${msg}`);
              })
              .finally(() => {
                fastSellConfirmRetryScheduledByMint.delete(mint);
              });
          }, 50);
        }
      },
    });
    console.log(`⚡ Mirror path completed in ${elapsed()}`);
    const tokenMint = tokenSideMint(result);
    if (result.side === "buy" && tokenMint) {
      persistentBuyLockCommitted = true;
      failedBuyRecoveryInFlightMints.delete(tokenMint);
      pendingBuyRetryByMint.delete(tokenMint);
    }
    const marketCapUsd = tokenMint ? await maybeGetMarketCap(tokenMint) : null;
    const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;
    if (
      result.side === "buy" &&
      tokenMint &&
      rustHotBuyRecordedByMint.has(tokenMint) &&
      mirrorBuySignatureByMint.get(tokenMint) !== result.signature
    ) {
      console.warn(
        `TypeScript buy ${result.signature.slice(0, 8)}... for ${tokenMint.slice(0, 8)}... ` +
          "was broadcast after Rust hot sniper already recorded this mint; skipping duplicate optimistic accounting."
      );
      return;
    }
    const preSellPosition = result.side === "sell" ? getPosition(result.input.meta.mint) : null;
    logTrade(result, swap, marketCapUsd, marketCapCheckedAt);
    const postSellPosition = result.side === "sell" ? getPosition(result.input.meta.mint) : null;
    if (!simulationMode) {
      if (result.side === "buy" && result.input.meta.mint === WRAPPED_SOL_MINT) {
        noteLocalSolDebit(result.input.amountRaw);
      } else if (result.side === "sell" && result.output.meta.mint === WRAPPED_SOL_MINT) {
        noteLocalSolCredit(result.output.amountRaw);
      }
    }
    if (result.side === "sell" && lockedFastSellMint) {
      if (result.confirmationPending) {
        fastSellLockShouldPersist = true;
      } else {
        fastSellConfirmPendingByMint.delete(lockedFastSellMint);
        lockedFastSellMint = null;
      }
    }
    const impactPct = result.priceImpactPct ? parseFloat(result.priceImpactPct) * 100 : null;
    // For mirrored sells we have realized PnL on the position now that recordSell ran inside logTrade.
    let realizedPnlLamports: number | undefined;
    let realizedPnlPct: number | undefined;
    if (result.side === "sell") {
      if (preSellPosition) {
        const fill = sellFillAccounting(preSellPosition, result.input.amountRaw, result.output.amountRaw);
        realizedPnlLamports = fill.pnlLamports;
        realizedPnlPct = fill.pnlPct;
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
      {
        priceImpactPct: impactPct,
        realizedPnlLamports,
        realizedPnlPct,
        positionRealizedPnlLamports: postSellPosition?.realizedPnlLamports,
      }
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
      if (isBuySignal(swap) && swap.skipDirectBuyRoutes) {
        quarantinedBuyMints.set(err.mint, `Jupiter rescue impact too high: ${err.impactPct.toFixed(1)}%`);
        pendingBuyRetryByMint.delete(err.mint);
        failedBuyRecoveryInFlightMints.delete(err.mint);
      }
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
    if (err instanceof BuyAmountBelowMinimumError) {
      console.log(
        `Skipping buy: ${err.mint.slice(0, 8)}... copy-sized amount ` +
          `${quoteAmountDisplay(err.calculatedRaw, err.quoteMint)} is below configured minimum ` +
          `${quoteAmountDisplay(err.minRaw, err.quoteMint)}.`
      );
      return;
    }
    const msg = err instanceof Error ? err.message : String(err);

    // Check if this is a sell failure that shouldn't count as PnL
    const isSellFailure = isSellSignal(swap) &&
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
    
    const failureClass = classifyExecutionError(msg);
    const retryDecision = decideRetryAction(msg);
    const isSellForTelemetry = isSellSignal(swap);
    recordExecutionTelemetrySync({
      kind: "mirror-failure",
      route: isSellForTelemetry ? "sell" : "buy",
      side: isSellForTelemetry ? "sell" : "buy",
      mint: isSellForTelemetry ? swap.inputMint : swap.outputMint,
      sourceSignature: swap.signature,
      wallet: swap.wallet,
      elapsedMs: Date.now() - ts,
      success: false,
      error: msg,
      errorClass: failureClass.category,
      errorCode: failureClass.code,
      recoverable: failureClass.recoverable,
      details: { retryAction: retryDecision.action, shouldQuarantine: retryDecision.shouldQuarantine },
    });
    console.error(`?? Mirror failed for ${swap.signature}: ${msg}`);
    if (isBuySignal(swap) && buyOncePerTokenEnabled && isPostBroadcastBuyUncertainty(msg)) {
      const failedMint = swap.outputMint;
      holdBuyOnceMintAfterBroadcastUncertainty(failedMint, msg);
      persistentBuyLockCommitted = true;
    } else if (isBuySignal(swap) && (swap.skipDirectBuyRoutes || retryDecision.shouldQuarantine || isTerminalBuyFailure(msg))) {
      const failedMint = swap.outputMint;
      quarantinedBuyMints.set(
        failedMint,
        `${swap.skipDirectBuyRoutes ? "Jupiter rescue failed" : retryDecision.action}: ${msg}`
      );
      pendingBuyRetryByMint.delete(failedMint);
      fastBuyConfirmRetryScheduledByMint.delete(failedMint);
      failedBuyRecoveryInFlightMints.delete(failedMint);
      console.warn(
        `Buy quarantine set for ${failedMint.slice(0, 8)}... after terminal buy failure. ` +
          "No more retries for this mint this session."
      );
    } else if (isBuySignal(swap) && !retryDecision.shouldQuarantine) {
      const failedMint = swap.outputMint;
      boughtMintsThisSession.delete(failedMint);
      liveBuyAttemptedMintsThisSession.delete(failedMint);
      pendingBuyRetryByMint.delete(failedMint);
      failedBuyRecoveryInFlightMints.delete(failedMint);
      console.warn(
        `Buy failure for ${failedMint.slice(0, 8)}... is recoverable (${retryDecision.action}); ` +
          "not quarantining so a later source signal can retry."
      );
    }
    
    // Send Telegram notification for general mirror errors
    const isSell = isSellSignal(swap);
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
        isBuySignal(swap) ? "buy" : isSellSignal(swap) ? "sell" : "swap",
      input: {
        mint: swap.inputMint,
        symbol: swap.inputMint === WRAPPED_SOL_MINT ? "SOL" : swap.inputMint === USDC_MINT ? "USDC" : shortMint(swap.inputMint),
        name: swap.inputMint === WRAPPED_SOL_MINT ? "Solana" : swap.inputMint === USDC_MINT ? "USD Coin" : shortMint(swap.inputMint),
        decimals: swap.inputMint === WRAPPED_SOL_MINT ? 9 : swap.inputMint === USDC_MINT ? 6 : 0,
        amountRaw: swap.amountRaw,
        display: swap.inputMint === WRAPPED_SOL_MINT || swap.inputMint === USDC_MINT
          ? quoteAmountDisplay(swap.amountRaw, swap.inputMint)
          : `${swap.amountRaw} raw`,
      },
      output: {
        mint: swap.outputMint,
        symbol: swap.outputMint === WRAPPED_SOL_MINT ? "SOL" : swap.outputMint === USDC_MINT ? "USDC" : shortMint(swap.outputMint),
        name: swap.outputMint === WRAPPED_SOL_MINT ? "Solana" : swap.outputMint === USDC_MINT ? "USD Coin" : shortMint(swap.outputMint),
        decimals: swap.outputMint === WRAPPED_SOL_MINT ? 9 : swap.outputMint === USDC_MINT ? 6 : 0,
        amountRaw: 0,
        display: "—",
      },
      isSimulated: simulationMode,
      error: msg,
    });
  } finally {
    const pendingMint = retryPendingMint;
    releaseBuyLocks();
    if (persistentBuyLockMint && !persistentBuyLockCommitted) {
      releasePersistentBuyLock(persistentBuyLockMint);
      persistentBuyLockMint = null;
    }
    if (lockedFastSellMint && !fastSellLockShouldPersist) {
      fastSellConfirmPendingByMint.delete(lockedFastSellMint);
      drainQueuedSourceSell(lockedFastSellMint);
      lockedFastSellMint = null;
    }
    queuedSellCapBySignature.delete(swap.signature);
    if (pendingMint) {
      const pending = pendingBuyRetryByMint.get(pendingMint);
      const existing = getPosition(pendingMint);
      if (
        pending &&
        !failedBuyRecoveryInFlightMints.has(pendingMint) &&
        !quarantinedBuyMints.has(pendingMint) &&
        !boughtMintsThisSession.has(pendingMint) &&
        !(existing && existing.buysCount > 0)
      ) {
        pendingBuyRetryByMint.delete(pendingMint);
        // Replay one follow-on source signal after a dead/dropped attempt releases the mint lock.
        setTimeout(() => {
          if (stopRequested || quarantinedBuyMints.has(pendingMint)) return;
          processSwap(pending, kp).catch((err) => {
            console.error("Queued buy retry failed:", err);
          });
        }, 0);
      } else if (pending) {
        pendingBuyRetryByMint.delete(pendingMint);
      }
    }
  }
}

async function loop(): Promise<void> {
  const kp = keypair!;
  const bootTimeSec = Math.floor((startedAt ?? Date.now()) / 1000);
  const watchedWallets = getWatchedWallets();
  let lastWarm = 0;
  let lastBalanceLog = 0;
  let lastRiskCheck = 0;
  let lastSourceHoldCheck = 0;
  let lastPoll = 0;

  while (!stopRequested) {
    try {
      // Poll is a safety net — WebSocket does the fast path. Run serially so polls catch anything the WS missed.
      const loopStartedAt = Date.now();
      if (loopStartedAt - lastPoll >= pollIntervalMs) {
        const swaps = await getNewSwaps(watchedWallets, processedSignatures, bootTimeSec);
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
      if (sourceHoldExitEnabled && now - lastSourceHoldCheck >= sourceHoldExitIntervalMs) {
        await checkSourceHoldExits();
        lastSourceHoldCheck = Date.now();
      }
    } catch (err) {
      console.error("Watcher error:", err);
    }

    if (stopRequested) break;
    // Loop floor must be small enough to honor the fastest tier; fall back to pollIntervalMs when risk is off.
    const sleepMs = Math.min(
      pollIntervalMs,
      riskControlsEnabled
        ? Math.min(riskFastIntervalMs, riskNearTriggerIntervalMs, riskNormalIntervalMs)
        : pollIntervalMs,
      sourceHoldExitEnabled ? sourceHoldExitIntervalMs : pollIntervalMs
    );
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
  if (unsubscribeRabbit) {
    try {
      await unsubscribeRabbit();
    } catch {
      // best-effort
    }
    unsubscribeRabbit = null;
  }
  if (unsubscribeRustSidecar) {
    try {
      await unsubscribeRustSidecar();
    } catch {
      // best-effort
    }
    unsubscribeRustSidecar = null;
  }
  if (unsubscribeHeliusTx) {
    try {
      await unsubscribeHeliusTx();
    } catch {
      // best-effort
    }
    unsubscribeHeliusTx = null;
  }

  running = false;
  startedAt = null;
  stopRequested = false;
  stopFeeOracle();
  stopJitoTipOracle();
  stopJitoConnectionWarmup();
  stopBlockhashCache();
  console.log("Bot stopped.");
}

export async function startBot(): Promise<void> {
  if (running) return;
  await validateLicense();
  if (trackedWallets.length === 0) {
    throw new Error("No TRACKED_WALLETS set in .env.");
  }
  keypair = Keypair.fromSecretKey(bs58.decode(getPrivateKeyBase58()));
  running = true;
  startedAt = null;
  stopRequested = false;
  resetCircuitBreaker();
  startFeeOracle();
  startJitoTipOracle();
  startJitoConnectionWarmup();
  startBlockhashCache();
  console.log(
    "Copytrading bot started. Our wallet:",
    keypair.publicKey.toBase58()
  );
  const watchedWallets = getWatchedWallets();
  console.log("Tracked wallets:", trackedWallets.length);
  console.log("Tracked wallet list:", trackedWallets.map(shortWallet).join(", "));
  if (exitSignalSellsEnabled && exitSignalWallets.length > 0) {
    console.log("Exit signal sells: ON");
    console.log("Exit signal wallet list:", exitSignalWallets.map(shortWallet).join(", "));
    console.log("Total watched wallets:", watchedWallets.length);
  } else {
    console.log("Exit signal sells: OFF");
  }
  if (clusterTriggerEnabled) {
    console.log(
      `Cluster trigger: ON — wallets ${clusterWallets.map(shortWallet).join(", ") || "(none)"}, ` +
        `min source buy ${formatSolAmount(clusterMinSourceBuyLamports)} SOL.`
    );
    console.log(
      clusterConfirmWallet
        ? `Cluster confirm wallet: ${shortWallet(clusterConfirmWallet)} ` +
            `(window ${clusterConfirmWindowMs}ms, ` +
            `no-confirm trailing ${clusterNoConfirmTrailingSteps.length > 0 ? clusterNoConfirmTrailingSteps.map((s) => `${s.peakPct}%→${s.floorPct >= 0 ? "+" : ""}${s.floorPct}%`).join(",") : "unchanged"}).`
        : "Cluster confirm wallet: (none)."
    );
  }

  // Send Telegram notification
  void telegram.notifyBotStatus("started");
  await updateBalance(keypair.publicKey);
  captureSessionStartBalance();
  if (simulationMode) {
    console.log(
      "Running in SIMULATION mode –",
      trackedWallets.length,
      "wallet(s); no transactions will be sent."
    );
  }

  if (riskControlsEnabled) {
    const trailDesc = trailingEnabled
      ? ` trailing=${trailingSteps.map((s) => `${s.peakPct}%→${s.floorPct >= 0 ? "+" : ""}${s.floorPct}%`).join(",")},`
      : "";
    const hybridDesc = hybridExitEnabled
      ? ` hybrid=${hybridExitTpSellPct}%@+${hybridExitTpPct}% then ${hybridExitSourceExitRemaining ? "source exit" : "normal exits"},`
      : "";
    console.log(
      `Risk exits enabled: SL ${stopLossPct ?? "off"}%, TP ${takeProfitPct ?? "off"}%,${trailDesc}${hybridDesc} ` +
        `tiers fast=${riskFastIntervalMs}ms (first ${riskFastWindowMs}ms) / near=${riskNearTriggerIntervalMs}ms (within ${riskNearTriggerPct}pp of trigger) / normal=${riskNormalIntervalMs}ms, ` +
        `drift=${riskDriftPctPerSec}pp/s, SL min hold ${minHoldBeforeStopLossMs}ms, max ${maxRiskQuotesPerCheck} quote(s)/tick.`
    );
  }
  if (sourceHoldExitEnabled) {
    console.log(
      `Source-hold exits enabled: check every ${sourceHoldExitIntervalMs}ms, ` +
        `${sourceHoldExitConsecutiveMisses} consecutive zero-balance checks, ` +
        `min position age ${sourceHoldExitMinPositionAgeMs}ms.`
    );
  }
  if (maxOpenPositions != null) {
    console.log(`Max open positions enabled: ${maxOpenPositions}.`);
  }
  if (maxSolPerTokenLamports != null) {
    console.log(`Max per-token deployed SOL enabled: ${formatSolAmount(maxSolPerTokenLamports)} SOL.`);
  }
  if (maxSessionDeployedLamports != null) {
    console.log(`Max session deployed SOL enabled: ${formatSolAmount(maxSessionDeployedLamports)} SOL.`);
  }
  if (maxOpenDeployedLamports != null) {
    console.log(`Max open deployed SOL enabled: ${formatSolAmount(maxOpenDeployedLamports)} SOL.`);
  }
  if (blockPumpfunMayhem) {
    console.log(`Pump.fun Mayhem Mode filter enabled${pumpfunMayhemStrict ? " (strict)" : ""}.`);
  }

  const startupCutoffSec = Math.floor(Date.now() / 1000);
  await warmSourcePreexistingBuyMints(startupCutoffSec);
  await Promise.allSettled([
    warmConnection(),
    warmPumpSwapStaticAccounts(),
    warmWalletAccounts(keypair.publicKey),
    warmExecutionAltAccounts(console.log),
  ]);
  startedAt = Date.now();
  console.log("Fresh start - only trades from this moment onward will be mirrored.");

  const kp = keypair;
  if (rustSidecarEnabled) {
    unsubscribeRustSidecar = await startRustPumpFunSidecar(
      processedSignatures,
      (swap) => {
        processSwap(swap, kp).catch((err) => {
          console.error("Rust sidecar processSwap error:", err);
        });
      },
      (hotBuy) => {
        handleRustHotBuy(hotBuy, kp).catch((err) => {
          console.error("Rust hot sniper bookkeeping error:", err);
        });
      },
      (fallback) => {
        handleRustHotBuyFallback(fallback);
      }
    );
  } else {
    console.log("Rust sidecar disabled (set RUST_SIDECAR_ENABLED=true to enable).");
  }

  if (shyftGrpcEnabled) {
    unsubscribeGrpc = await subscribeGrpcWalletSwaps(
      watchedWallets,
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
  if (shyftRabbitStreamEnabled) {
    unsubscribeRabbit = await subscribeRabbitWalletSwaps(
      watchedWallets,
      processedSignatures,
      (swap) => {
        processSwap(swap, kp).catch((err) => {
          console.error("RabbitStream processSwap error:", err);
        });
      }
    );
  }

  if (heliusTxSubscribeEnabled) {
    unsubscribeHeliusTx = await subscribeHeliusTransactionSwaps(
      watchedWallets,
      processedSignatures,
      (swap) => {
        processSwap(swap, kp).catch((err) => {
          console.error("Helius transactionSubscribe processSwap error:", err);
        });
      }
    );
  } else {
    console.log("Helius transactionSubscribe disabled (set HELIUS_TX_SUBSCRIBE_ENABLED=true to enable).");
  }

  unsubscribeWs = await subscribeWalletSwaps(
    watchedWallets,
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
  pendingBuyRetryByMint.clear();
  rustHotSniperPrimaryPendingMints.clear();
  rustHotSniperPrimaryFallbackSources.clear();
  rustHotBuyRecordedByMint.clear();
  rustHotBuyRecordedSourceSignatures.clear();
  fastBuyConfirmRetryScheduledByMint.clear();
  failedBuyRecoveryInFlightMints.clear();
  boughtMintsThisSession.clear();
  liveBuyAttemptedMintsThisSession.clear();
  quarantinedBuyMints.clear();
  sourceBuySignatureByMint.clear();
  pendingFastBuyConfirmSignaturesByMint.clear();
  fastBuyLocalCloseGuardUntilByMint.clear();
  loggedFastBuyDebitBySignature.clear();
  openPositionBuyMintsInFlight.clear();
  riskSellsInFlight.clear();
  exitSignalSellsInFlight.clear();
  riskSellRetryAfterByMint.clear();
  sourceHoldMissingByMint.clear();
  sourceSignalStateByMint.clear();
  confirmedInventoryByMint.clear();
  queuedSourceSellByMint.clear();
  queuedSellCapBySignature.clear();
  confirmedPostSellStateByMint.clear();
  postSellRemainderChecksInFlight.clear();
  queuedPostSellRemainderByMint.clear();
  fastSellConfirmPendingByMint.clear();
  riskTraceByMint.clear();
  riskQuoteStateByMint.clear();
  hybridExitTpDoneByMint.clear();
  hybridExitTpInFlightByMint.clear();
  lastCurveRiskValueByMint.clear();
  captureSessionStartBalance();
  resetCircuitBreaker();
  console.log("?? Cleared all positions, trade history, and signature cache.");
}

async function sellSimulatedPosition(
  position: PositionSnapshot,
  note?: string,
  quotedOutSolLamports?: number,
  amountRawOverride?: number
): Promise<void> {
  const sellRaw = Math.max(
    1,
    Math.min(position.heldRaw, Math.floor(amountRawOverride ?? position.heldRaw))
  );
  let outSolLamports =
    quotedOutSolLamports ??
    (await quoteRiskExitLamports(position.mint, sellRaw));
  outSolLamports = applyShadowSellLamports(outSolLamports, `sim-sell:${position.mint}:${Date.now()}`);
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
  const snap = recordSell(meta, sellRaw, outSolLamports);
  cleanupHybridExitStateIfClosed(snap);
  const avgEntry = position.totalBoughtRaw > 0 ? position.totalSolSpentLamports / position.totalBoughtRaw : 0;
  const sellCostBasisLamports = avgEntry * sellRaw;
  const fillPnlLamports = outSolLamports - sellCostBasisLamports;
  const finalPnlPct =
    sellCostBasisLamports > 0
      ? ((outSolLamports - sellCostBasisLamports) /
          sellCostBasisLamports) *
        100
      : 0;
  const marketCapUsd = await maybeGetMarketCap(position.mint);
  const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;

  console.log(`Sold (simulated): ${formatTokenAmount(sellRaw, position.decimals)} $${position.symbol}`);
  console.log(`Received: ${formatSolAmount(outSolLamports)} SOL`);
  console.log(`Fill PnL: ${formatPnl(fillPnlLamports)}`);
  console.log(`Position PnL: ${formatPnl(snap.realizedPnlLamports)}`);
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
        amountRaw: sellRaw,
        display: `${formatTokenAmount(sellRaw, position.decimals)} $${position.symbol}`,
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
      realizedPnlLamports: fillPnlLamports,
      realizedPnlPct: finalPnlPct,
      positionRealizedPnlLamports: snap.realizedPnlLamports,
      exitReason: note,
    }
  );
  recordTradeOutcome(fillPnlLamports);

  void telegram.notifyTrade({
    type: "sell",
    symbol: position.symbol,
    amount: formatTokenAmount(sellRaw, position.decimals),
    solAmount: formatSolAmount(outSolLamports),
    marketCap: marketCapUsd != null ? formatMarketCap(marketCapUsd) : undefined,
    marketCapCheckedAt: marketCapCheckedAt ?? undefined,
    isSimulated: true,
    wallet: note ? "risk" : "manual",
    tokenAddress: position.mint,
    pnl: formatPnl(fillPnlLamports),
    positionPnl: formatPnl(snap.realizedPnlLamports),
    note,
    quotedPnlPct: note ? `${finalPnlPct >= 0 ? "+" : ""}${finalPnlPct.toFixed(1)}%` : undefined,
  });
}

/**
 * Sell specific position by index (#1, #2, etc)
 */
export async function sellPositionByIndex(index: number): Promise<void> {
  await sellPositionPercentByIndex(index, 1);
}

export async function sellPositionPercentByIndex(index: number, fraction: number): Promise<void> {
  const open = getPositions().filter((p) => p.heldRaw > 0);
  const clampedFraction = Math.min(1, Math.max(0.000001, fraction));
  
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
  const targetRaw =
    clampedFraction >= 0.999999
      ? position.heldRaw
      : Math.floor(position.totalBoughtRaw * clampedFraction);
  const sellRaw = Math.min(position.heldRaw, Math.max(1, targetRaw));
  const pctLabel = clampedFraction >= 0.999999 ? "100%" : `${Math.round(clampedFraction * 100)}%`;
  if (simulationMode) {
    await sellSimulatedPosition(position, clampedFraction >= 0.999999 ? undefined : `manual ${pctLabel} trim`, undefined, sellRaw);
    return;
  }

  if (!keypair) {
    throw new Error("Wallet is not loaded yet. Start the bot before selling positions.");
  }

  try {
    await sellLivePosition(position, undefined, sellRaw);
  } catch (error) {
    void telegram.notifyError(
      `Failed to sell ${pctLabel} of position #${index} ($${position.symbol})`,
      error instanceof Error ? (error as Error).message : String(error)
    );
    throw error;
  }

  return;
}

async function getVerifiedTokenBalance(
  owner: PublicKey,
  mint: string,
  opts: {
    symbol?: string;
    expectedRaw?: number;
    decimals?: number;
    ownerLabel?: string;
  } = {}
): Promise<number> {
  const fast = await getTokenBalanceFast(owner, mint);
  if (fast == null) return getTokenBalance(owner, mint);
  if (fast > 0) return fast;

  const scanned = await getTokenBalance(owner, mint);
  if (scanned > 0) {
    const label = opts.symbol ? `$${opts.symbol}` : mint.slice(0, 8);
    console.warn(
      `${label}: fast ${opts.ownerLabel ?? "wallet"} token balance read 0, ` +
        `but full account scan found ${
          opts.decimals == null ? scanned.toString() : formatTokenAmount(scanned, opts.decimals)
        }. Using scanned balance.`
    );
    return scanned;
  }

  if ((opts.expectedRaw ?? 0) > 0) {
    await sleep(ZERO_TOKEN_BALANCE_RECHECK_MS);
    const retryFast = await getTokenBalanceFast(owner, mint);
    if (retryFast != null && retryFast > 0) return retryFast;
    const retryScanned = await getTokenBalance(owner, mint);
    if (retryScanned > 0) {
      const label = opts.symbol ? `$${opts.symbol}` : mint.slice(0, 8);
      console.warn(
        `${label}: zero-balance recheck recovered ${
          opts.decimals == null ? retryScanned.toString() : formatTokenAmount(retryScanned, opts.decimals)
        } on ${opts.ownerLabel ?? "wallet"}.`
      );
      return retryScanned;
    }
  }

  return 0;
}

/**
 * Sell all open positions (simulated or live based on mode)
 */
async function sellLivePosition(
  p: PositionSnapshot,
  note?: string,
  amountRawOverride?: number,
  requireFreshRiskReason = true
): Promise<void> {
  p = await reconcileOpenPositionWithConfirmedBuy(p);
  if (amountRawOverride == null) {
    const latest = getPosition(p.mint);
    if (latest && latest.heldRaw > p.heldRaw) {
      console.warn(
        `$${p.symbol}: position grew while sell was pending; refreshing full-sell amount ` +
          `${formatTokenAmount(p.heldRaw, p.decimals)} -> ${formatTokenAmount(latest.heldRaw, latest.decimals)}.`
      );
      p = latest;
    }
  }
  let effectiveNote = note;
  let liveHeldRaw: number;
  try {
    liveHeldRaw = await refreshConfirmedInventory(p.mint);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    throw new Error(
      `Confirmed inventory lookup failed for $${p.symbol}; sell not broadcast: ${msg}`
    );
  }
  if (liveHeldRaw === 0) {
    console.warn(
      `$${p.symbol}: verified wallet balance is 0; historical buy transactions will not be used as sell inventory.`
    );
  }
  // Chain authoritatively says we hold 0 (read succeeded, value was 0). Typical causes: transfer-fee /
  // honeypot mechanics drained the ATA, or an earlier sell landed but timed out before we recorded it.
  // Falling back to the tracked amount here is what was burning gas on every 15s risk-exit retry —
  // we'd broadcast a doomed swap that fails on-chain with Custom(6024) at the destination AMM.
  // Close the ghost position locally instead so the risk loop drops it from the candidate pool.
  if (liveHeldRaw === 0 && p.heldRaw > 0) {
    if (hasFastBuyConfirmationPending(p.mint)) {
      console.warn(
        `$${p.symbol}: wallet holds 0 on-chain while fast-mode buy confirmation is still pending; ` +
          "leaving optimistic position open for background confirmation cleanup."
      );
      return;
    }
    if (Date.now() - p.lastTradeAt < confirmationNullStatusTimeoutMs) {
      console.warn(
        `$${p.symbol}: wallet holds 0 on-chain during fast-mode confirmation grace window; ` +
          "leaving optimistic position open for background confirmation cleanup."
      );
      return;
    }
    if (hasFastBuyLocalCloseGuard(p.mint)) {
      console.warn(
        `$${p.symbol}: wallet holds 0 on-chain during recent fast-buy settle guard; ` +
          "leaving optimistic position open instead of closing locally."
      );
      return;
    }
    console.warn(
      `$${p.symbol}: wallet holds 0 on-chain but tracker shows ${formatTokenAmount(p.heldRaw, p.decimals)}. ` +
        `Closing position locally — no broadcast (likely transfer-fee/honeypot token or earlier unrecorded sell).`
    );
    const snap = recordSell(
      { mint: p.mint, symbol: p.symbol, name: p.symbol, decimals: p.decimals, isSol: false },
      p.heldRaw,
      0
    );
    cleanupHybridExitStateIfClosed(snap);
    return;
  }
  const isSourceHoldExit = effectiveNote?.startsWith("Source-hold exit:") ?? false;
  const isFullExitIntent = amountRawOverride == null;
  const fullExitRemainderIntent: PostSellRemainderIntent | null = isFullExitIntent
    ? {
        kind: "source-full",
        sourceSellVersion: sourceSignalStateByMint.get(p.mint)?.version ?? 0,
      }
    : null;
  if (isFullExitIntent && liveHeldRaw > p.heldRaw) {
    if (adjustBuyTokenAmount(p.mint, p.heldRaw, liveHeldRaw)) {
      const latest = getPosition(p.mint);
      if (latest) p = latest;
    }
    console.warn(
      `Full exit will sell full live $${p.symbol} balance. ` +
        `live=${formatTokenAmount(liveHeldRaw, p.decimals)}, tracked=${formatTokenAmount(p.heldRaw, p.decimals)}.`
    );
  }
  const sellRaw =
    amountRawOverride != null
      ? Math.min(Math.max(1, Math.floor(amountRawOverride)), liveHeldRaw, p.heldRaw)
      : isFullExitIntent && liveHeldRaw > 0
      ? liveHeldRaw
      : liveHeldRaw > 0
      ? Math.min(liveHeldRaw, p.heldRaw)
      : p.heldRaw;
  if (liveHeldRaw > p.heldRaw) {
    console.warn(
      `Live balance for $${p.symbol} exceeds tracked position; selling tracked amount only. ` +
        `live=${formatTokenAmount(liveHeldRaw, p.decimals)}, tracked=${formatTokenAmount(p.heldRaw, p.decimals)}`
    );
  }
  if (sellRaw <= 0) {
    throw new Error(`No token balance available for $${p.symbol}.`);
  }
  if (fastSellConfirmPendingByMint.has(p.mint)) {
    console.log(`Risk/manual sell skipped for $${p.symbol}: previous fast sell is still awaiting confirmation.`);
    return;
  }

  const avgEntry = p.totalBoughtRaw > 0 ? p.totalSolSpentLamports / p.totalBoughtRaw : 0;
  const sellCostBasisLamports = avgEntry * sellRaw;

  if (effectiveNote && requireFreshRiskReason) {
    const freshOutLamports = await quoteRiskExitLamports(p.mint, sellRaw);
    const tickAt = Date.now();
    const freshPnlPct =
      sellCostBasisLamports > 0
        ? ((freshOutLamports - sellCostBasisLamports) / sellCostBasisLamports) * 100
        : 0;
    const prevPeak = riskQuoteStateByMint.get(p.mint)?.peakPnlPct ?? -Infinity;
    const peakPnlPct = Math.max(prevPeak, freshPnlPct);
    riskQuoteStateByMint.set(p.mint, {
      lastPnlPct: freshPnlPct,
      lastQuotedAt: tickAt,
      lastTickedAt: tickAt,
      peakPnlPct,
    });

    const freshReason = riskExitReason(
      freshPnlPct,
      tickAt - p.firstSeenAt,
      peakPnlPct,
      activeTrailingSteps(p.mint)
    );
    if (!freshReason) {
      console.log(
        `Risk exit stale for $${p.symbol}; fresh quote is ${freshPnlPct >= 0 ? "+" : ""}${freshPnlPct.toFixed(1)}% ` +
          "vs avg entry, so no sell was broadcast."
      );
      return;
    }
    effectiveNote = freshReason;
  }

  const isHybridPartialSell = (effectiveNote ?? "").startsWith("Hybrid TP hit");
  let result: MirrorResult;
  try {
    fastSellConfirmPendingByMint.add(p.mint);
    result = await mirrorSwap(
      {
        signature: sourceBuySignatureByMint.get(p.mint) ?? `manual-sell-${p.mint}`,
        wallet: "manual-sell",
        inputMint: p.mint,
        outputMint: WRAPPED_SOL_MINT,
        amountRaw: sellRaw,
        isNativeInput: false,
      },
      keypair!,
      {
        exactAmountRaw: sellRaw,
        onFastSellConfirmSuccess: ({ mint, tokenAmountRaw, solLamports, signature }) => {
          reconcileFastSellExecution({
            mint,
            expectedTokenAmountRaw: tokenAmountRaw,
            expectedSolLamports: solLamports,
            signature,
          }).catch((err) => {
            const msg = err instanceof Error ? err.message : String(err);
            console.warn(`Fast sell reconciliation failed for ${mint.slice(0, 8)}…: ${msg}`);
          }).finally(() => {
            if (fullExitRemainderIntent) {
              schedulePostSellRemainderCheck(mint, fullExitRemainderIntent);
            } else {
              clearFastSellConfirmationPendingSoon(mint);
            }
          });
        },
        onFastSellConfirmFailure: ({ mint, tokenAmountRaw, solLamports, reason }) => {
          const rolledBack = unrecordSell(mint, tokenAmountRaw, solLamports);
          if (isHybridPartialSell) {
            hybridExitTpDoneByMint.delete(mint);
            hybridExitTpInFlightByMint.delete(mint);
          }
          if (rolledBack) {
            console.warn(
              `🧹 Phantom sell cleaned up: ${mint.slice(0, 8)}… ` +
              `(reason: ${reason}). Sell removed from tracking.`
            );
          }
          expandQueuedSellAfterFailure(mint);
          releaseFastSellLifecycle(mint);
        },
      }
    );
    if (!result.confirmationPending) {
      fastSellConfirmPendingByMint.delete(p.mint);
    }
  } catch (err) {
    fastSellConfirmPendingByMint.delete(p.mint);
    throw err;
  }
  const soldRaw = result.input.amountRaw;
  const receivedLamports = result.output.amountRaw;
  const snap = recordSell(result.input.meta, soldRaw, receivedLamports);
  cleanupHybridExitStateIfClosed(snap);
  const marketCapUsd = await maybeGetMarketCap(p.mint);
  const marketCapCheckedAt = marketCapUsd != null ? Date.now() : null;
  const exitPrice = tokenPriceInSol(receivedLamports, soldRaw, p.decimals);
  const exitPerRaw = soldRaw > 0 ? receivedLamports / soldRaw : 0;
  const pct = avgEntry > 0 ? (exitPerRaw / avgEntry - 1) * 100 : 0;
  const filledCostBasisLamports = avgEntry * soldRaw;
  const filledPnlPct =
    filledCostBasisLamports > 0
      ? ((receivedLamports - filledCostBasisLamports) / filledCostBasisLamports) * 100
      : pct;
  const filledPnlLamports = receivedLamports - filledCostBasisLamports;
  const quotedPnlPct =
    filledCostBasisLamports > 0
      ? `${filledPnlPct >= 0 ? "+" : ""}${filledPnlPct.toFixed(1)}%`
      : undefined;

  console.log(`Sell LIVE (manual): ${formatTokenAmount(soldRaw, p.decimals)} $${p.symbol}`);
  console.log(`Received: ${formatSolAmount(receivedLamports)} SOL`);
  console.log(`Exit: ${formatPrice(exitPrice)} SOL per $${p.symbol} (${pct >= 0 ? "+" : ""}${pct.toFixed(1)}% vs avg entry)`);
  console.log(`CA: ${p.mint}`);
  if (marketCapUsd != null) console.log(`MC: ${formatMarketCap(marketCapUsd)}`);
  console.log(`Tx: ${result.signature}`);
  console.log(`Fill PnL on $${p.symbol}: ${formatPnl(filledPnlLamports)}`);
  console.log(`Position PnL on $${p.symbol}: ${formatPnl(snap.realizedPnlLamports)}`);
  if (effectiveNote) console.log(`Reason: ${effectiveNote}`);
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
    pnl: formatPnl(filledPnlLamports),
    positionPnl: formatPnl(snap.realizedPnlLamports),
    note: effectiveNote,
    quotedPnlPct: effectiveNote ? quotedPnlPct : undefined,
    txSignature: result.signature,
  });

  pushSwap(
    {
      ts: Date.now(),
      sourceSignature: `manual-sell-${p.mint}`,
      wallet: effectiveNote ? "risk-sell" : "manual-sell",
      side: "sell",
      input: toLegView(result.input),
      output: toLegView(result.output),
      isSimulated: false,
      marketCapUsd,
      marketCapCheckedAt,
      mirrorSignature: result.signature,
    },
    {
      realizedPnlLamports: filledPnlLamports,
      realizedPnlPct: filledPnlPct,
      positionRealizedPnlLamports: snap.realizedPnlLamports,
      exitReason: effectiveNote,
      priceImpactPct: result.priceImpactPct ? parseFloat(result.priceImpactPct) * 100 : null,
    }
  );
  recordTradeOutcome(filledPnlLamports);
}

async function sellLiveWalletTokenBalance(mint: string, amountRaw: number): Promise<void> {
  if (!keypair) {
    throw new Error("Wallet is not loaded yet. Start the bot before selling wallet dust.");
  }
  const meta = await getTokenMeta(mint);
  const tracked = getPosition(mint);
  if (fastSellConfirmPendingByMint.has(mint)) {
    console.log(
      `Wallet-balance sell skipped for ${mint.slice(0, 8)}...: another sell is awaiting confirmation.`
    );
    return;
  }
  fastSellConfirmPendingByMint.add(mint);
  let confirmedBalance: number;
  try {
    confirmedBalance = await refreshConfirmedInventory(mint);
  } catch (err) {
    releaseFastSellLifecycle(mint);
    throw err;
  }
  const exactRaw = Math.min(Math.max(0, Math.floor(amountRaw)), confirmedBalance);
  if (exactRaw <= 0) {
    releaseFastSellLifecycle(mint);
    return;
  }
  const optimisticTrackedRaw = tracked
    ? Math.min(exactRaw, Math.max(0, tracked.heldRaw))
    : 0;
  console.log(`Dust sell: ${formatTokenAmount(exactRaw, meta.decimals)} $${meta.symbol} (${mint})`);

  let result: MirrorResult;
  try {
    result = await mirrorSwap(
      {
        signature: `manual-dust-sell-${mint}`,
        wallet: "manual-dust-sell",
        inputMint: mint,
        outputMint: WRAPPED_SOL_MINT,
        amountRaw: exactRaw,
        isNativeInput: false,
      },
      keypair,
      {
        exactAmountRaw: exactRaw,
        onFastSellConfirmSuccess: ({ mint: confirmedMint, tokenAmountRaw, solLamports, signature }) => {
          void reconcileFastSellExecution({
            mint: confirmedMint,
            expectedTokenAmountRaw: tokenAmountRaw,
            expectedSolLamports: solLamports,
            signature,
          }).finally(() => clearFastSellConfirmationPendingSoon(confirmedMint));
        },
        onFastSellConfirmFailure: ({ mint: failedMint, tokenAmountRaw, solLamports, reason }) => {
          const trackedSol =
            tokenAmountRaw > 0
              ? Math.floor((solLamports * optimisticTrackedRaw) / tokenAmountRaw)
              : 0;
          if (
            optimisticTrackedRaw > 0 &&
            unrecordSell(failedMint, optimisticTrackedRaw, trackedSol)
          ) {
            console.warn(
              `Phantom wallet-balance sell cleaned up: ${failedMint.slice(0, 8)}... (${reason}).`
            );
          }
          expandQueuedSellAfterFailure(failedMint);
          releaseFastSellLifecycle(failedMint);
        },
      }
    );
    if (!result.confirmationPending) releaseFastSellLifecycle(mint);
  } catch (err) {
    releaseFastSellLifecycle(mint);
    throw err;
  }

  const soldRaw = result.input.amountRaw;
  const receivedLamports = result.output.amountRaw;
  const trackedSellRaw = tracked ? Math.min(soldRaw, Math.max(0, tracked.heldRaw)) : 0;
  let realizedPnlLamports: number | undefined;
  let realizedPnlPct: number | undefined;

  if (tracked && trackedSellRaw > 0) {
    const avgEntry = tracked.totalBoughtRaw > 0 ? tracked.totalSolSpentLamports / tracked.totalBoughtRaw : 0;
    const allocatedSol = soldRaw > 0 ? Math.floor((receivedLamports * trackedSellRaw) / soldRaw) : 0;
    const snap = recordSell(result.input.meta, trackedSellRaw, allocatedSol);
    const costBasis = avgEntry * trackedSellRaw;
    realizedPnlLamports = allocatedSol - costBasis;
    realizedPnlPct = costBasis > 0 ? (realizedPnlLamports / costBasis) * 100 : undefined;
    console.log(
      `Tracked close: ${formatTokenAmount(trackedSellRaw, meta.decimals)} $${meta.symbol}; ` +
        `Position PnL ${formatPnl(snap.realizedPnlLamports)}`
    );
  } else {
    console.log("Dust was not an open tracked position; PnL tracker left unchanged.");
  }

  console.log(`Dust sell received: ${formatSolAmount(receivedLamports)} SOL`);
  console.log(`Tx: ${result.signature}`);
  console.log("");

  pushSwap(
    {
      ts: Date.now(),
      sourceSignature: `manual-dust-sell-${mint}`,
      wallet: "manual-dust-sell",
      side: "sell",
      input: toLegView(result.input),
      output: toLegView(result.output),
      isSimulated: result.isSimulated,
      mirrorSignature: result.signature,
    },
    {
      realizedPnlLamports,
      realizedPnlPct,
      positionRealizedPnlLamports: getPosition(mint)?.realizedPnlLamports,
      exitReason: "manual wallet dust cleanup",
      priceImpactPct: result.priceImpactPct ? parseFloat(result.priceImpactPct) * 100 : null,
    }
  );
}

export async function sellAllWalletDust(): Promise<void> {
  if (simulationMode) {
    console.log("Wallet dust cleanup is live-wallet only. Use 'a' or 'e' for simulation exits.");
    return;
  }
  if (!keypair) {
    throw new Error("Wallet is not loaded yet. Start the bot before selling wallet dust.");
  }

  const balances = (await getWalletTokenBalances(keypair.publicKey)).filter((b) => {
    if (b.mint === WRAPPED_SOL_MINT || b.mint === USDC_MINT || b.amountRaw <= 0) return false;
    if (hasFastBuyConfirmationPending(b.mint)) {
      console.log(
        `Dust cleanup skipping ${b.mint.slice(0, 8)}... because a fast buy confirmation is still pending.`
      );
      return false;
    }
    const tracked = getPosition(b.mint);
    if (tracked && tracked.heldRaw > 0) {
      console.log(
        `Dust cleanup skipping active tracked position $${tracked.symbol} ` +
          `(${formatTokenAmount(tracked.heldRaw, tracked.decimals)} held). Use 'a' or position hotkeys to sell it.`
      );
      return false;
    }
    return true;
  });
  if (balances.length === 0) {
    console.log("No nonzero wallet token balances/dust found.");
    return;
  }

  console.log(`Selling ${balances.length} untracked wallet dust/token balance(s) (LIVE)...`);
  console.log("");
  for (const balance of balances) {
    try {
      await sellLiveWalletTokenBalance(balance.mint, balance.amountRaw);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Dust sell failed for ${balance.mint.slice(0, 8)}...: ${msg}`);
    }
  }
  await printPnlReport();
}

/**
 * Compute the effective stop threshold for a position given its peak PnL.
 * Starts at -stopLossPct (the base SL), then walks the trailing steps in ascending order;
 * each step the peak has cleared raises the threshold to its `floorPct`.
 * Returns the threshold as a signed % (e.g. -25 means "exit if pnl <= -25%", +5 means "+5% lock").
 */
function effectiveStopPct(peakPnlPct: number, steps: TrailingStep[] = trailingSteps): number | null {
  if (stopLossPct == null) return null;
  let threshold = -stopLossPct;
  for (const step of steps) {
    if (peakPnlPct >= step.peakPct) {
      threshold = Math.max(threshold, step.floorPct);
    } else {
      break; // steps are sorted asc; no later step can have triggered
    }
  }
  return threshold;
}

function riskExitReason(
  pnlPct: number,
  positionAgeMs: number,
  peakPnlPct: number,
  steps: TrailingStep[] = trailingSteps
): string | null {
  if (takeProfitPct != null && pnlPct >= takeProfitPct) {
    return `Take profit hit (${pnlPct.toFixed(1)}% >= ${takeProfitPct}%)`;
  }
  const threshold = effectiveStopPct(peakPnlPct, steps);
  if (
    threshold != null &&
    pnlPct <= threshold &&
    positionAgeMs >= minHoldBeforeStopLossMs
  ) {
    if (steps.length > 0 && threshold > -(stopLossPct ?? 0)) {
      return `Trailing stop hit (${pnlPct.toFixed(1)}% <= ${threshold >= 0 ? "+" : ""}${threshold.toFixed(1)}%, peak ${peakPnlPct.toFixed(1)}%)`;
    }
    return `Stop loss hit (${pnlPct.toFixed(1)}% <= ${threshold.toFixed(1)}%)`;
  }
  return null;
}

function hybridExitSellRaw(position: PositionSnapshot, pnlPct: number): number | null {
  if (!hybridExitEnabled) return null;
  if (hybridExitTpDoneByMint.has(position.mint) || hybridExitTpInFlightByMint.has(position.mint)) {
    return null;
  }
  if (hybridExitTpPct <= 0 || hybridExitTpSellPct <= 0) return null;
  if (position.heldRaw <= 1 || pnlPct < hybridExitTpPct) return null;

  const targetRaw = Math.floor((position.heldRaw * hybridExitTpSellPct) / 100);
  return Math.min(position.heldRaw, Math.max(1, targetRaw));
}

function cleanupHybridExitStateIfClosed(position: PositionSnapshot): void {
  if (position.heldRaw <= 0) {
    hybridExitTpDoneByMint.delete(position.mint);
    hybridExitTpInFlightByMint.delete(position.mint);
  }
}

function isJupiterNoRoutesError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /NO_ROUTES_FOUND|Could not find any route|no routes/i.test(msg);
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
  const effSL = effectiveStopPct(state.peakPnlPct, activeTrailingSteps(p.mint));

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

  pruneClusterState();
  const open = getPositions().filter((p) => p.heldRaw > 0);
  if (open.length === 0) return;

  // First pass: pick the positions that are due to quote this tick.
  const now = Date.now();
  const candidates: { p: PositionSnapshot; bucket: RiskBucket }[] = [];
  for (let i = 0; i < open.length; i++) {
    const p = open[(riskPositionCursor + i) % open.length]!;
    if (riskSellsInFlight.has(p.mint)) continue;
    if (fastSellConfirmPendingByMint.has(p.mint)) continue;
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

  for (let p of positionsToCheck) {
    if (riskSellsInFlight.has(p.mint)) continue;
    if (fastSellConfirmPendingByMint.has(p.mint)) continue;
    if ((riskSellRetryAfterByMint.get(p.mint) ?? 0) > Date.now()) continue;
    p = await reconcileOpenPositionWithConfirmedBuy(p);
    if (p.costBasisRemainingLamports <= 0) continue;

    try {
      let outSolLamports = await quoteRiskExitLamports(p.mint, p.heldRaw);
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
      const hybridSellRaw = hybridExitSellRaw(p, pnlPct);
      if (hybridSellRaw != null) {
        const reason =
          `Hybrid TP hit (${pnlPct.toFixed(1)}% >= ${hybridExitTpPct}%), ` +
          `selling ${hybridExitTpSellPct.toFixed(1)}%; remaining position follows source exit`;
        riskSellsInFlight.add(p.mint);
        hybridExitTpInFlightByMint.add(p.mint);
        console.log(`Hybrid exit triggered for $${p.symbol}: ${reason}`);
        console.log(
          `Selling ${formatTokenAmount(hybridSellRaw, p.decimals)} of ` +
            `${formatTokenAmount(p.heldRaw, p.decimals)} $${p.symbol}.`
        );
        try {
          if (simulationMode) {
            await sellSimulatedPosition(p, reason, undefined, hybridSellRaw);
          } else {
            await sellLivePosition(p, reason, hybridSellRaw, false);
          }
          hybridExitTpDoneByMint.add(p.mint);
          riskSellRetryAfterByMint.delete(p.mint);
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          riskSellRetryAfterByMint.set(p.mint, Date.now() + RISK_SELL_FAILURE_COOLDOWN_MS);
          void telegram.notifyRiskExit({
            status: "failed",
            symbol: p.symbol,
            reason,
            quotedSol: "partial",
            costBasisSol: formatSolAmount(p.costBasisRemainingLamports),
            pnlPct: `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(1)}%`,
            error: msg,
            tokenAddress: p.mint,
            isSimulated: simulationMode,
          });
          throw err;
        } finally {
          hybridExitTpInFlightByMint.delete(p.mint);
          riskSellsInFlight.delete(p.mint);
        }
        continue;
      }
      const reason = riskExitReason(
        pnlPct,
        tickAt - p.firstSeenAt,
        peakPnlPct,
        activeTrailingSteps(p.mint)
      );
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

async function sourceWalletTokenBalanceSnapshots(
  sourceWallets: PublicKey[]
): Promise<Map<string, WalletTokenBalanceSnapshot>> {
  const snapshots = new Map<string, WalletTokenBalanceSnapshot>();
  for (const source of sourceWallets) {
    const wallet = source.toBase58();
    const snapshot = await getWalletTokenBalanceSnapshot(source);
    snapshots.set(wallet, snapshot);
    if (snapshot.failedPrograms > 0) {
      console.warn(
        `Source-hold wallet snapshot incomplete for ${shortWallet(wallet)}: ` +
          `${snapshot.successfulPrograms}/2 token programs read successfully.`
      );
    }
  }
  return snapshots;
}

function sourceWalletTotalTokenBalance(
  sourceWallets: PublicKey[],
  snapshots: Map<string, WalletTokenBalanceSnapshot>,
  mint: string
): {
  totalRaw: number;
  checked: number;
  errors: number;
} {
  let totalRaw = 0;
  let checked = 0;
  let errors = 0;
  for (const source of sourceWallets) {
    const snapshot = snapshots.get(source.toBase58());
    if (!snapshot) {
      errors++;
      continue;
    }
    totalRaw += snapshot.balancesByMint.get(mint) ?? 0;
    if (snapshot.failedPrograms > 0 || snapshot.successfulPrograms < 2) {
      errors++;
      continue;
    }
    checked++;
  }
  return { totalRaw, checked, errors };
}

async function checkSourceHoldExits(): Promise<void> {
  if (!sourceHoldExitEnabled) return;
  if (!keypair) return;

  const sourceWallets = trackedSourcePublicKeys();
  if (sourceWallets.length === 0) return;

  const now = Date.now();
  const open = getPositions().filter((p) => p.heldRaw > 0);
  if (open.length === 0) {
    sourceHoldMissingByMint.clear();
    return;
  }

  const openMints = new Set(open.map((p) => p.mint));
  for (const mint of [...sourceHoldMissingByMint.keys()]) {
    if (!openMints.has(mint)) sourceHoldMissingByMint.delete(mint);
  }

  const eligible: PositionSnapshot[] = [];
  for (const p of open) {
    if (now - p.firstSeenAt < sourceHoldExitMinPositionAgeMs) continue;
    if (riskSellsInFlight.has(p.mint)) continue;
    if (exitSignalSellsInFlight.has(p.mint)) continue;
    if (fastSellConfirmPendingByMint.has(p.mint)) continue;
    if ((riskSellRetryAfterByMint.get(p.mint) ?? 0) > now) continue;
    eligible.push(p);
  }

  if (eligible.length === 0) return;

  const snapshots = await sourceWalletTokenBalanceSnapshots(sourceWallets);

  for (const p of eligible) {
    const sourceBalance = sourceWalletTotalTokenBalance(sourceWallets, snapshots, p.mint);
    if (sourceBalance.totalRaw > 0) {
      sourceHoldMissingByMint.delete(p.mint);
      continue;
    }

    if (sourceBalance.checked === 0 || sourceBalance.errors > 0) {
      console.warn(
        `Source-hold exit: source wallet snapshot incomplete for ${p.mint.slice(0, 8)}...; ` +
          `not counting as a miss.`
      );
      continue;
    }

    const misses = (sourceHoldMissingByMint.get(p.mint) ?? 0) + 1;
    sourceHoldMissingByMint.set(p.mint, misses);
    console.log(
      `Source-hold exit check: tracked source holds 0 $${p.symbol} ` +
        `(${misses}/${sourceHoldExitConsecutiveMisses} consecutive misses).`
    );

    if (misses < sourceHoldExitConsecutiveMisses) continue;

    const reason =
      `Source-hold exit: tracked source wallet no longer holds token ` +
      `(${misses}/${sourceHoldExitConsecutiveMisses} checks)`;
    riskSellsInFlight.add(p.mint);
    console.log(`Source-hold exit triggered for $${p.symbol}: ${reason}`);
    try {
      if (simulationMode) {
        await sellSimulatedPosition(p, reason);
      } else {
        await sellLivePosition(p, reason, undefined, false);
      }
      sourceHoldMissingByMint.delete(p.mint);
      riskSellRetryAfterByMint.delete(p.mint);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      riskSellRetryAfterByMint.set(p.mint, Date.now() + RISK_SELL_FAILURE_COOLDOWN_MS);
      void telegram.notifyRiskExit({
        status: "failed",
        symbol: p.symbol,
        reason,
        costBasisSol: formatSolAmount(p.costBasisRemainingLamports),
        error: msg,
        tokenAddress: p.mint,
        isSimulated: simulationMode,
      });
      throw err;
    } finally {
      riskSellsInFlight.delete(p.mint);
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
    await printPnlReport();
    return;
  }

  for (const p of open) {
    try {
      let outSolLamports = await quoteRiskExitLamports(p.mint, p.heldRaw);
      const sellImpactStr = "";
      if (!Number.isFinite(outSolLamports) || outSolLamports <= 0) {
        console.warn(`?? Skipped $${p.symbol}: no liquidity for quote.`);
        continue;
      }

      if (simulationMode) {
        outSolLamports = applyShadowSellLamports(outSolLamports, `sim-sell-all:${p.mint}:${Date.now()}`);
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
        const quote = await getQuote(p.mint, WRAPPED_SOL_MINT, p.heldRaw, 80);
        const swapResponse = await getSwap(quote, keypair!.publicKey.toBase58());
        const transaction = await createSenderTransactionFromSwapResponse(
          swapResponse as { swapTransaction: string; [key: string]: unknown },
          keypair!,
          { route: "jupiter-sell" }
        );
        
        const signature = await broadcastTransactionWithSender(transaction, {
          route: "jupiter-sell",
          side: "sell",
          mint: p.mint,
        });
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

  await printPnlReport();
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
    await printPnlReport();
    return;
  }

  console.log(`🏁 Closing ${open.length} open position(s) at current market prices...`);
  console.log("");

  for (const p of open) {
    try {
      let outSolLamports = await quoteRiskExitLamports(p.mint, p.heldRaw);
      const sellImpactStr = "";
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

  await printPnlReport();
}

export function isRunning(): boolean {
  return running;
}

export async function printPnlReport(): Promise<void> {
  if (keypair) {
    await updateBalance(keypair.publicKey, { log: false });
  }

  const totals = getTotals();
  const positions = getPositions();
  const walletPnl = getSessionWalletPnlSnapshot();
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
  const pnlBasisLabel =
    pnlAccountingBasis === "dex" ? "DEX/trade basis" : "wallet cashflow basis";
  console.log(`PnL basis: ${pnlBasisLabel}`);
  console.log(`💸 SOL spent: ${formatSolAmount(totals.totalSolSpentLamports)} SOL`);
  console.log(`💰 SOL received: ${formatSolAmount(totals.totalSolReceivedLamports)} SOL`);
  console.log(`Realized proceeds: ${formatSolAmount(totals.realizedProceedsLamports)} SOL (costed sells only)`);
  const realizedRoiPct =
    totals.realizedCostBasisLamports > 0
      ? (totals.realizedPnlLamports / totals.realizedCostBasisLamports) * 100
      : null;
  console.log(
    `📊 Realized PnL: ${formatPnl(totals.realizedPnlLamports)} ` +
      `(sold cost ${formatSolAmount(totals.realizedCostBasisLamports)} SOL` +
      `${realizedRoiPct == null ? "" : `, ${realizedRoiPct >= 0 ? "+" : ""}${realizedRoiPct.toFixed(1)}%`}; ${pnlBasisLabel})`
  );
  console.log(`Open cost basis: ${formatSolAmount(totals.openCostBasisLamports)} SOL (open positions not counted in realized PnL)`);
  if (totals.uncostedProceedsLamports > 0) {
    console.log(
      `Uncosted sell proceeds: ${formatSolAmount(totals.uncostedProceedsLamports)} SOL ` +
        `(excluded from realized PnL; sell exceeded recorded inventory)`
    );
  }

  if (walletPnl.deltaLamports != null) {
    console.log(`Wallet SOL delta: ${formatPnl(walletPnl.deltaLamports)} (all-in fees/tips/rent included)`);
    console.log(
      `Balance: ${formatSolAmount(walletPnl.startBalanceLamports ?? 0)} -> ` +
        `${formatSolAmount(walletPnl.currentBalanceLamports ?? 0)} SOL`
    );
  } else if (!simulationMode) {
    console.log("Wallet SOL delta: unavailable until start/current balances are known");
  }

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
        p.realizedCostBasisLamports > 0
          ? (pnlLamports / p.realizedCostBasisLamports) * 100
          : 0;
      const pctStr =
        p.realizedCostBasisLamports > 0
          ? ` (${pctNum >= 0 ? "+" : ""}${pctNum.toFixed(1)}%)`
          : "";

      console.log(`${pnlIcon} $${p.symbol}`);
      console.log(`   Status:    ${statusTag}`);
      console.log(`   PnL:       ${formatPnl(pnlLamports)}${pctStr} (${pnlBasisLabel})`);
      console.log(`   Spent:     ${formatSolAmount(p.totalSolSpentLamports)} SOL  (${p.buysCount} buy${p.buysCount === 1 ? "" : "s"})`);
      console.log(`   Received:  ${formatSolAmount(p.totalSolReceivedLamports)} SOL  (${p.sellsCount} sell${p.sellsCount === 1 ? "" : "s"})`);
      console.log(`   Sold cost: ${formatSolAmount(p.realizedCostBasisLamports)} SOL`);
      if (p.uncostedProceedsLamports > 0) {
        console.log(`   Uncosted:  ${formatSolAmount(p.uncostedProceedsLamports)} SOL proceeds excluded from PnL`);
      }
      if (isOpen) console.log(`   Open cost: ${formatSolAmount(p.costBasisRemainingLamports)} SOL`);
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
    walletPnl: walletPnl.deltaLamports != null ? formatPnl(walletPnl.deltaLamports) : undefined,
    unrealizedPnl: "not marked",
    totalPnl: formatPnl(totals.realizedPnlLamports),
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
    exitSignalSellsEnabled,
    exitSignalWallets,
    simulationMode,
    buysPaused,
    maxOpenPositions: maxOpenPositions ?? null,
    maxSolPerTokenSol: maxSolPerTokenLamports != null ? maxSolPerTokenLamports / LAMPORTS_PER_SOL : null,
    maxSessionDeployedSol:
      maxSessionDeployedLamports != null ? maxSessionDeployedLamports / LAMPORTS_PER_SOL : null,
    maxOpenDeployedSol:
      maxOpenDeployedLamports != null ? maxOpenDeployedLamports / LAMPORTS_PER_SOL : null,
    minBuySol: minBuyLamports / LAMPORTS_PER_SOL,
    maxBuySol: maxBuyLamports / LAMPORTS_PER_SOL,
    copyBuyPercentage,
    solReserveSol: solReserveLamports / LAMPORTS_PER_SOL,
    tipLamports,
    priorityFeeMicroLamports,
    mirrorSlippageBps,
    feeGuide: {
      computeUnitLimit,
      useJitoDirect,
      useJitoFallback,
      heliusTipMinLamports: HELIUS_TIP_MIN_LAMPORTS,
    },
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
    sessionStartBalanceLamports,
    sessionWalletDeltaLamports: getSessionWalletPnlSnapshot().deltaLamports,
    recentSwaps: recentSwaps.slice(),
    positions: getPositions().filter((p) => p.heldRaw > 0),
    totals: getTotals(),
  };
}


