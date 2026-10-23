import { Keypair } from "@solana/web3.js";
import {
  minBuyLamports,
  maxBuyLamports,
  minBuyUsdcRaw,
  maxBuyUsdcRaw,
  simulationMode,
  fastMode,
  mirrorSlippageBps,
  mirrorRetrySlippageBps,
  maxEntryPriceImpactPct,
  maxImpactReduceAttempts,
  maxSourceDriftPct,
  pumpfunDirectEnabled,
  pumpSwapDirectBuyEnabled,
  jupiterFallbackEnabled,
  mirrorGraduatedBuys,
  jupiterOversizeMaxAccounts,
  mirrorQuoteAsset,
  copyBuyPercentage,
  minBuyBehavior,
  localPumpfunSolPriceUsd,
} from "./config.js";
import { getQuote, getSwap, type QuoteResponse } from "./jupiter.js";
import {
  createSenderTransactionFromSwapResponse,
  broadcastTransactionWithSender,
} from "./sender.js";
import { confirmSignature, isConfirmationTimeoutError } from "./confirm.js";
import { getTokenBalance, getTokenBalanceFast } from "./balance.js";
import { getTokenMeta, WRAPPED_SOL_MINT, USDC_MINT, type TokenMeta } from "./tokenRegistry.js";
import { getPosition, unrecordBuy } from "./pnl.js";
import type { DetectedSwap } from "./types.js";
import { tryPumpFunDirectBuy, tryPumpFunDirectSell } from "./pumpfun/route.js";
import { tryPumpSwapDirectBuy, tryPumpSwapDirectSell } from "./pumpswap/route.js";
import { tryRaydiumCpmmDirectBuy, tryRaydiumCpmmDirectSell } from "./raydiumCpmm/route.js";
import { applyShadowExecution, shadowDelay, shadowSimulateTransaction } from "./shadowExecution.js";
import { recordExecutionStage } from "./executionTelemetry.js";

export type Side = "buy" | "sell" | "swap";

export type MirrorResult = {
  signature: string;
  isSimulated: boolean;
  side: Side;
  input: { meta: TokenMeta; amountRaw: number };
  output: { meta: TokenMeta; amountRaw: number };
  /** SOL-denominated cost basis for buys, even when the input asset is USDC. */
  costBasisLamports?: number;
  /** Jupiter's reported price impact as a decimal string (e.g. "0.0234" = 2.34%). */
  priceImpactPct?: string;
  /** Fast mode may return after broadcast before the signature lands on chain. */
  confirmationPending?: boolean;
};

function isUncertainConfirmationReason(reason: string): boolean {
  const normalized = reason.toLowerCase();
  return (
    normalized.includes("not found in status polling") ||
    normalized.includes("confirmation timed out")
  );
}

export type MirrorSwapOptions = {
  persistentBuyLockOwner?: string;
  exactAmountRaw?: number;
  allowBagsDirectJupiter?: boolean;
  onFastBuyConfirmFailure?: (args: {
    mint: string;
    tokenAmountRaw: number;
    solLamports: number;
    reason: string;
    positionRemoved: boolean;
    sourceSwap?: DetectedSwap;
  }) => void;
  onFastBuyConfirmSuccess?: (args: {
    mint: string;
    tokenAmountRaw: number;
    solLamports: number;
    signature: string;
    sourceSwap?: DetectedSwap;
  }) => void;
  onFastSellConfirmFailure?: (args: {
    mint: string;
    tokenAmountRaw: number;
    solLamports: number;
    reason: string;
    sourceSwap?: DetectedSwap;
  }) => void;
  onFastSellConfirmSuccess?: (args: {
    mint: string;
    tokenAmountRaw: number;
    solLamports: number;
    signature: string;
    sourceSwap?: DetectedSwap;
  }) => void;
};

export class NoPositionError extends Error {
  readonly mint: string;
  constructor(mint: string) {
    super(`No position held for ${mint}, skipping sell`);
    this.mint = mint;
    this.name = "NoPositionError";
  }
}

export class AlreadyBoughtError extends Error {
  readonly mint: string;
  constructor(mint: string) {
    super(`Already bought ${mint} this session, skipping duplicate buy`);
    this.mint = mint;
    this.name = "AlreadyBoughtError";
  }
}

export class ImpactTooHighError extends Error {
  readonly mint: string;
  readonly impactPct: number;
  readonly capPct: number;
  constructor(mint: string, impactPct: number, capPct: number) {
    super(
      `Entry impact ${impactPct.toFixed(1)}% exceeds cap ${capPct.toFixed(1)}% even at MIN_BUY_SOL — skipping`
    );
    this.mint = mint;
    this.impactPct = impactPct;
    this.capPct = capPct;
    this.name = "ImpactTooHighError";
  }
}

export class SourceDriftError extends Error {
  readonly mint: string;
  readonly driftPct: number;
  readonly capPct: number;
  constructor(mint: string, driftPct: number, capPct: number) {
    super(
      `Source drift ${driftPct.toFixed(1)}% > cap ${capPct.toFixed(1)}% — chasing, skipping`
    );
    this.mint = mint;
    this.driftPct = driftPct;
    this.capPct = capPct;
    this.name = "SourceDriftError";
  }
}

function quoteImpactPct(quote: QuoteResponse): number {
  const impact = quote.priceImpactPct ? parseFloat(quote.priceImpactPct) * 100 : 0;
  return Number.isFinite(impact) ? impact : 0;
}

function assertEntryImpactWithinCap(mint: string, quote: QuoteResponse): void {
  if (maxEntryPriceImpactPct == null) return;
  const impactPct = quoteImpactPct(quote);
  if (impactPct > maxEntryPriceImpactPct) {
    throw new ImpactTooHighError(mint, impactPct, maxEntryPriceImpactPct);
  }
}

/**
 * Probe-and-shrink: keep re-quoting at smaller sizes until Jupiter's reported price impact is at or
 * below the configured cap. Saves the last valid quote so the slippage-ladder phase can reuse it.
 *
 * Only runs for buys when MAX_ENTRY_PRICE_IMPACT_PCT is set. Sells aren't probed because we can't
 * reduce them — we have to exit the entire position.
 */
async function probeBuySizeForImpact(
  inputMint: string,
  outputMint: string,
  initialAmountRaw: number
): Promise<{ amountRaw: number; quote: QuoteResponse }> {
  let probed = initialAmountRaw;
  let lastQuote: QuoteResponse | undefined;
  let lastImpactPct = 0;
  const minAmountRaw = inputMint === USDC_MINT ? minBuyUsdcRaw : minBuyLamports;

  for (let attempt = 0; attempt <= maxImpactReduceAttempts; attempt++) {
    const quote = await getQuote(inputMint, outputMint, probed, mirrorSlippageBps);
    const impactPct = quoteImpactPct(quote);
    lastQuote = quote;
    lastImpactPct = impactPct;

    // Jupiter sometimes returns NaN/undefined for synthetic or no-route quotes — treat as pass.
    if (!Number.isFinite(impactPct) || maxEntryPriceImpactPct == null || impactPct <= maxEntryPriceImpactPct) {
      return { amountRaw: probed, quote };
    }

    console.warn(
      `Entry impact ${impactPct.toFixed(1)}% > cap ${maxEntryPriceImpactPct.toFixed(1)}% at ${formatQuoteAmount(probed, inputMint)}`
    );

    if (probed <= minAmountRaw || attempt === maxImpactReduceAttempts) break;

    // Reduce size proportional to overshoot, but never less than 0.25× and never more than 0.6× of current.
    // This avoids both "too small to matter" and "barely smaller, will probably fail again."
    const targetFraction = Math.max(0.25, Math.min(0.6, maxEntryPriceImpactPct / impactPct));
    const next = Math.max(minAmountRaw, Math.floor(probed * targetFraction));
    if (next >= probed) break;
    console.log(
      `Reducing buy size: ${formatQuoteAmount(probed, inputMint)} -> ${formatQuoteAmount(next, inputMint)}`
    );
    probed = next;
  }

  // We exhausted attempts or hit MIN_BUY without satisfying the cap.
  if (lastQuote && (lastImpactPct <= (maxEntryPriceImpactPct ?? Infinity))) {
    return { amountRaw: probed, quote: lastQuote };
  }
  throw new ImpactTooHighError(outputMint, lastImpactPct, maxEntryPriceImpactPct ?? 0);
}

function classifySide(inputMint: string, outputMint: string): Side {
  if ((inputMint === WRAPPED_SOL_MINT || inputMint === USDC_MINT) && outputMint !== WRAPPED_SOL_MINT && outputMint !== USDC_MINT) return "buy";
  if (outputMint === WRAPPED_SOL_MINT || outputMint === USDC_MINT) return "sell";
  return "swap";
}

function quoteMintForBuy(swap: DetectedSwap): string {
  if (mirrorQuoteAsset === "SOL") return WRAPPED_SOL_MINT;
  if (mirrorQuoteAsset === "USDC") return USDC_MINT;
  return swap.inputMint === USDC_MINT ? USDC_MINT : WRAPPED_SOL_MINT;
}

function formatQuoteAmount(raw: number, quoteMint: string): string {
  return quoteMint === USDC_MINT
    ? `${(raw / 1_000_000).toFixed(2)} USDC`
    : `${(raw / 1_000_000_000).toFixed(4)} SOL`;
}

function applyCopyBuyPercentage(amountRaw: number): number {
  if (copyBuyPercentage <= 0) return 0;
  return Math.floor(amountRaw * (copyBuyPercentage / 100));
}

function isSlippageFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return (
    msg.includes("6001") ||
    msg.includes("SlippageToleranceExceeded") ||
    msg.toLowerCase().includes("slippage")
  );
}

/**
 * Detect transient broadcast errors where retrying with a fresh blockhash is reasonable.
 * Covers expired blockhashes, "block not available", and network-layer fetch failures.
 * NOT slippage-related — those are handled separately so the slippage ladder still drives.
 */
function isTransientBroadcastFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  return (
    lower.includes("suspected dropped transaction") ||
    lower.includes("not found in status polling") ||
    lower.includes("blockhash not found") ||
    lower.includes("transaction expired") ||
    lower.includes("block height exceeded") ||
    lower.includes("econnreset") ||
    lower.includes("etimedout") ||
    lower.includes("fetch failed") ||
    lower.includes("network") ||
    lower.includes("timeout")
  );
}

function isPacketSizeFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /1232B packet limit|encoding overruns|too large|packet/i.test(msg);
}

function isNoRouteFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /NO_ROUTES_FOUND|No routes found/i.test(msg);
}

function shouldTryPumpSwapAfterPumpFunSkip(reason: string, swap: DetectedSwap): boolean {
  const lower = reason.toLowerCase();
  return (
    lower.includes("graduated") ||
    lower.includes("not a pump.fun bonding-curve token") ||
    swap.sourcePumpSwapAccounts != null ||
    swap.outputMint.endsWith("pump")
  );
}

/**
 * The Rust hot sniper buys/reserves a mint via the shared buy-once gate. When the TypeScript
 * pump.fun direct path is skipped for that reason, the position is already owned by Rust — the
 * mirror must NOT chase it with a PumpSwap/Jupiter fallback (which fails with "pool not found"
 * on fresh pump mints and surfaces a false "Buy failed" + Telegram alert).
 */
function isRustAlreadyBoughtReason(reason: string): boolean {
  return /already bought\/reserved by rust|already hot-bought by rust|already bought .* this session|source signature already mirrored by rust/i.test(
    reason
  );
}

function shouldAllowJupiterAfterPumpSwapFailure(swap: DetectedSwap): boolean {
  // Pump.fun mints that have graduated should still be copied through PumpSwap. Jupiter often
  // has no route yet, and the constrained-route ladder adds seconds of dead latency on hot coins.
  return !swap.outputMint.endsWith("pump");
}

function estimateCostBasisLamports(inputMint: string, inputAmountRaw: number): number {
  if (inputMint === WRAPPED_SOL_MINT) return inputAmountRaw;
  if (inputMint !== USDC_MINT || inputAmountRaw <= 0) return inputAmountRaw;
  // Keep accounting conversion local. One raw USDC unit is 1e-6 USD and one
  // lamport is 1e-9 SOL, hence raw * 1000 / USD-per-SOL.
  if (localPumpfunSolPriceUsd != null && localPumpfunSolPriceUsd > 0) {
    return Math.max(1, Math.round((inputAmountRaw * 1000) / localPumpfunSolPriceUsd));
  }
  return inputAmountRaw;
}

export class BuyAmountBelowMinimumError extends Error {
  readonly mint: string;
  readonly calculatedRaw: number;
  readonly minRaw: number;
  readonly quoteMint: string;
  constructor(mint: string, calculatedRaw: number, minRaw: number, quoteMint: string) {
    super(`Copy buy amount below configured minimum for ${mint}`);
    this.mint = mint;
    this.calculatedRaw = calculatedRaw;
    this.minRaw = minRaw;
    this.quoteMint = quoteMint;
    this.name = "BuyAmountBelowMinimumError";
  }
}

async function getMirrorAmount(
  side: Side,
  swap: DetectedSwap,
  keypair: Keypair,
  options: MirrorSwapOptions
): Promise<number> {
  if (side === "buy") {
    const quoteMint = quoteMintForBuy(swap);
    const maxRaw = quoteMint === USDC_MINT ? maxBuyUsdcRaw : maxBuyLamports;
    const minRaw = quoteMint === USDC_MINT ? minBuyUsdcRaw : minBuyLamports;
    const percentageRaw = applyCopyBuyPercentage(swap.amountRaw);
    const fixedSizeMode = copyBuyPercentage <= 0;
    const calculatedRaw = fixedSizeMode ? minRaw : percentageRaw;

    console.log("[CopyTrade]");
    console.log("Tracked Wallet Buy Detected");
    console.log(`Token: ${swap.outputMint}`);
    console.log(`Tracked Wallet Amount: ${formatQuoteAmount(swap.amountRaw, quoteMint)}`);
    console.log(fixedSizeMode ? "Copy Percentage: fixed-size mode" : `Copy Percentage: ${copyBuyPercentage}%`);
    console.log(`Bot Buy Amount: ${formatQuoteAmount(calculatedRaw, quoteMint)}`);

    if (!Number.isFinite(calculatedRaw) || calculatedRaw <= 0) {
      throw new BuyAmountBelowMinimumError(swap.outputMint, calculatedRaw, minRaw, quoteMint);
    }

    if (!Number.isFinite(minRaw) || minRaw <= 0 || !Number.isFinite(maxRaw) || maxRaw <= 0) {
      throw new Error(
        `Invalid buy sizing config for ${swap.outputMint}: min=${String(minRaw)} max=${String(maxRaw)}`
      );
    }

    if (calculatedRaw < minRaw) {
      if (minBuyBehavior === "skip") {
        throw new BuyAmountBelowMinimumError(swap.outputMint, calculatedRaw, minRaw, quoteMint);
      }
      if (minBuyBehavior === "allow") {
        console.log(
          `Copy-sized buy is below configured minimum, allowing because MIN_BUY_BEHAVIOR=allow.`
        );
      }
    }

    const unclampedRaw =
      calculatedRaw < minRaw && minBuyBehavior === "floor" ? minRaw : calculatedRaw;
    const amountRaw = Math.min(maxRaw, unclampedRaw);
    if (!Number.isFinite(amountRaw) || amountRaw <= 0) {
      throw new Error(
        `Invalid mirrored buy amount for ${swap.outputMint}: source=${String(swap.amountRaw)} calculated=${String(calculatedRaw)} amount=${String(amountRaw)}`
      );
    }

    if (calculatedRaw < minRaw && minBuyBehavior === "floor") {
      console.log(
        `Trade floored: ${formatQuoteAmount(calculatedRaw, quoteMint)} -> ${formatQuoteAmount(amountRaw, quoteMint)} (min: ${formatQuoteAmount(minRaw, quoteMint)})`
      );
    } else if (calculatedRaw > maxRaw) {
      console.log(
        `Trade capped: ${formatQuoteAmount(calculatedRaw, quoteMint)} -> ${formatQuoteAmount(amountRaw, quoteMint)} (max: ${formatQuoteAmount(maxRaw, quoteMint)})`
      );
    }

    return amountRaw;
  }

  if (side === "sell") {
    if (options.exactAmountRaw != null && options.exactAmountRaw > 0) {
      return options.exactAmountRaw;
    }

    let amountRaw: number;
    if (simulationMode) {
      amountRaw = getPosition(swap.inputMint)?.heldRaw ?? 0;
    } else {
      // Fast path: direct ATA read. Falls back to scan-all on null (e.g., unusual token program).
      const fast = await getTokenBalanceFast(keypair.publicKey, swap.inputMint);
      amountRaw = fast ?? (await getTokenBalance(keypair.publicKey, swap.inputMint));
      if (amountRaw <= 0) {
        const tracked = getPosition(swap.inputMint);
        if (
          tracked &&
          tracked.heldRaw > 0 &&
          tracked.sellsCount === 0 &&
          Date.now() - tracked.firstSeenAt < 120_000
        ) {
          console.warn(
            `Mirror sell balance read 0 for ${swap.inputMint.slice(0, 8)}...; ` +
              `using recent tracked fast-buy amount ${tracked.heldRaw}.`
          );
          amountRaw = tracked.heldRaw;
        }
      }
    }

    if (amountRaw <= 0) {
      throw new NoPositionError(swap.inputMint);
    }

    return amountRaw;
  }

  throw new Error(`Unsupported token-to-token swap (input ${swap.inputMint})`);
}

export async function mirrorSwap(
  swap: DetectedSwap,
  keypair: Keypair,
  options: MirrorSwapOptions = {}
): Promise<MirrorResult> {
  const startedAt = Date.now();
  const elapsed = () => `${Date.now() - startedAt}ms`;
  const side = classifySide(swap.inputMint, swap.outputMint);
  const mirrorInputMint = side === "buy" ? quoteMintForBuy(swap) : swap.inputMint;
  const mirrorOutputMint = side === "sell" ? WRAPPED_SOL_MINT : swap.outputMint;
  const mirrorSwap = {
    ...swap,
    inputMint: mirrorInputMint,
    outputMint: mirrorOutputMint,
    isNativeInput: mirrorInputMint === WRAPPED_SOL_MINT,
  };
  const initialAmountRaw = await getMirrorAmount(side, swap, keypair, options);

  if (
    side === "buy" &&
    Boolean(swap.sourceRaydiumCpmmAccounts?.length) &&
    !swap.skipDirectBuyRoutes
  ) {
    const raydium = await tryRaydiumCpmmDirectBuy({
      user: keypair,
      mint: swap.outputMint,
      sourceSignature: swap.signature,
      sourceInputRaw: swap.sourceQuoteAmountRaw ?? swap.amountRaw,
      sourceOutputRaw: swap.counterpartyAmountRaw,
      sourceRaydiumCpmmAccounts: swap.sourceRaydiumCpmmAccounts,
      quoteMint: mirrorInputMint,
      quoteInRaw: initialAmountRaw,
      slippageBps: mirrorSlippageBps,
      startedAt,
      log: (m) => console.log(m),
      onConfirmFailure: (mint, tokensRaw, quoteAmountRaw, reason) => {
        const solLamports = estimateCostBasisLamports(mirrorInputMint, quoteAmountRaw);
        const removed = isUncertainConfirmationReason(reason)
          ? false
          : unrecordBuy(mint, tokensRaw, solLamports);
        options.onFastBuyConfirmFailure?.({
          mint,
          tokenAmountRaw: tokensRaw,
          solLamports,
          reason,
          positionRemoved: removed,
          sourceSwap: swap,
        });
      },
      onConfirmSuccess: (mint, tokensRaw, quoteAmountRaw, signature) => {
        const solLamports = estimateCostBasisLamports(mirrorInputMint, quoteAmountRaw);
        options.onFastBuyConfirmSuccess?.({
          mint,
          tokenAmountRaw: tokensRaw,
          solLamports,
          signature,
          sourceSwap: swap,
        });
      },
    });
    if (raydium.ok) {
      console.log(`Raydium CPMM direct buy complete (${elapsed()})`);
      await shadowDelay();
      return applyShadowExecution({
        signature: raydium.signature,
        isSimulated: raydium.isSimulated,
        side: "buy",
        input: { meta: raydium.inputMeta, amountRaw: Number(raydium.inputAmount) },
        output: { meta: raydium.outputMeta, amountRaw: Number(raydium.outputAmount) },
        costBasisLamports: estimateCostBasisLamports(mirrorInputMint, Number(raydium.inputAmount)),
        confirmationPending: raydium.confirmationPending,
      }, swap.signature);
    }
    console.log(`Raydium CPMM direct buy skipped: ${raydium.reason}; ${jupiterFallbackEnabled ? "falling back to Jupiter." : "Jupiter fallback disabled."}`);
  }
  // USDC-quoted PumpSwap buys bypass Pump.fun (bonding curves are SOL-only).
  // Rust debits the existing USDC ATA directly; no Jupiter or HTTP quote is used.
  if (
    side === "buy" &&
    mirrorInputMint === USDC_MINT &&
    pumpSwapDirectBuyEnabled &&
    Boolean(swap.sourcePumpSwapAccounts?.length) &&
    !swap.skipDirectBuyRoutes
  ) {
    const pumpSwap = await tryPumpSwapDirectBuy({
      user: keypair,
      mint: swap.outputMint,
      sourceSignature: swap.signature,
      buyLockOwner: options.persistentBuyLockOwner,
      sourceSolInLamports: swap.amountRaw,
      sourceTokensOutRaw: swap.counterpartyAmountRaw,
      sourcePumpSwapInstruction: swap.sourcePumpSwapInstruction,
      sourcePumpSwapAccounts: swap.sourcePumpSwapAccounts,
      quoteMint: USDC_MINT,
      solInLamports: initialAmountRaw,
      slippageBps: mirrorSlippageBps,
      startedAt,
      log: (m) => console.log(m),
    });
    if (pumpSwap.ok) {
      const usdcMeta = await getTokenMeta(USDC_MINT);
      console.log(`PumpSwap USDC direct path complete (${elapsed()})`);
      await shadowDelay();
      return applyShadowExecution({
        signature: pumpSwap.signature,
        isSimulated: pumpSwap.isSimulated,
        side: "buy",
        input: { meta: usdcMeta, amountRaw: Number(pumpSwap.quoteIn) },
        output: { meta: pumpSwap.mintMeta, amountRaw: Number(pumpSwap.baseOut) },
        costBasisLamports: estimateCostBasisLamports(USDC_MINT, Number(pumpSwap.quoteIn)),
        confirmationPending: pumpSwap.confirmationPending,
      }, swap.signature);
    }
    throw new Error(`Rust PumpSwap USDC buy failed: ${pumpSwap.reason}`);
  }

  // Phase 0: Direct pump.fun bonding-curve route. Bypasses Jupiter entirely for pre-graduation
  // pump.fun tokens — solves the "Jupiter says 100% impact" indexing-lag problem and shaves
  // 150-300ms off the entry path. Falls through to Jupiter only when fallback is enabled.
  if (
    side === "buy" &&
    pumpfunDirectEnabled &&
    (mirrorInputMint === WRAPPED_SOL_MINT ||
      (mirrorInputMint === USDC_MINT && (swap.sourceIxAccounts?.length ?? 0) >= 27)) &&
    !swap.skipDirectBuyRoutes
  ) {
    const direct = await tryPumpFunDirectBuy({
      user: keypair,
      mint: swap.outputMint,
      quoteMint: mirrorInputMint,
      solInLamports: initialAmountRaw,
      slippageBps: mirrorSlippageBps,
      maxImpactPct: maxEntryPriceImpactPct,
      minSolLamports: minBuyLamports,
      startedAt,
      log: (m) => console.log(m),
      sourceSignature: swap.forceDerivedPumpFunBuy ? undefined : swap.signature,
      rustSourceSignature:
        swap.buyRecoveryAttempt != null && swap.buyRecoveryAttempt > 0
          ? `${swap.signature}:rescue:${swap.buyRecoveryAttempt}`
          : undefined,
      sourceIxAccounts: swap.forceDerivedPumpFunBuy ? undefined : swap.sourceIxAccounts,
      sourceIxDataBase64: swap.forceDerivedPumpFunBuy ? undefined : swap.sourceIxDataBase64,
      onConfirmFailure: (mint, tokensRaw, quoteAmountRaw, reason) => {
        const solLamports = estimateCostBasisLamports(mirrorInputMint, quoteAmountRaw);
        // Phantom-position cleanup: in fastMode we recorded the buy before confirmation.
        // If the tx failed on chain, roll back so the risk loop stops trying to sell
        // tokens we never actually received.
        const removed = isUncertainConfirmationReason(reason)
          ? false
          : unrecordBuy(mint, tokensRaw, solLamports);
        if (removed) {
          console.warn(
            `🧹 Phantom buy cleaned up: ${mint.slice(0, 8)}… (reason: ${reason}). Position removed from tracking.`
          );
        }
        options.onFastBuyConfirmFailure?.({
          mint,
          tokenAmountRaw: tokensRaw,
          solLamports,
          reason,
          positionRemoved: removed,
          sourceSwap: swap,
        });
      },
      onConfirmSuccess: (mint, tokensRaw, quoteAmountRaw, signature) => {
        const solLamports = estimateCostBasisLamports(mirrorInputMint, quoteAmountRaw);
        options.onFastBuyConfirmSuccess?.({
          mint,
          tokenAmountRaw: tokensRaw,
          solLamports,
          signature,
          sourceSwap: swap,
        });
      },
    });
    if (direct.ok) {
      const quoteMeta = await getTokenMeta(mirrorInputMint);
      console.log(`Pump.fun direct path complete (${elapsed()})`);
      await shadowDelay();
      return applyShadowExecution({
        signature: direct.signature,
        isSimulated: direct.isSimulated,
        side: "buy",
        input: { meta: quoteMeta, amountRaw: Number(direct.solIn) },
        output: { meta: direct.mintMeta, amountRaw: Number(direct.tokensOut) },
        costBasisLamports: estimateCostBasisLamports(mirrorInputMint, Number(direct.solIn)),
        priceImpactPct: (direct.priceImpactPct / 100).toString(),
        confirmationPending: direct.confirmationPending,
      }, swap.signature);
    }
    if (isRustAlreadyBoughtReason(direct.reason)) {
      // Rust hot sniper already owns this mint this session. Treat as a benign skip
      // (handled by AlreadyBoughtError in the caller) instead of chasing a PumpSwap
      // fallback that fails with "pool not found" and emits a false buy failure.
      throw new AlreadyBoughtError(swap.outputMint);
    }
    if (mirrorInputMint === USDC_MINT) {
      throw new Error(`Rust Pump.fun V2 USDC buy failed: ${direct.reason}`);
    }
    if (!direct.recoverable) {
      // Hard fail (broadcast error, curve too thin) — surface it; don't try Jupiter for this trade.
      throw new Error(`Pump.fun direct buy failed: ${direct.reason}`);
    }
    // Recoverable: graduated token, not on curve, etc. Try PumpSwap first, then optional Jupiter.
    console.log(
      `Pump.fun direct path skipped: ${direct.reason}; ` +
        (jupiterFallbackEnabled ? "falling back to local PumpSwap/Jupiter if needed." : "Jupiter fallback disabled.")
    );
    if (pumpSwapDirectBuyEnabled && shouldTryPumpSwapAfterPumpFunSkip(direct.reason, swap)) {
      const pumpSwap = await tryPumpSwapDirectBuy({
        user: keypair,
        mint: swap.outputMint,
        sourceSignature: swap.signature,
        buyLockOwner: options.persistentBuyLockOwner,
        sourceSolInLamports: swap.amountRaw,
        sourceTokensOutRaw: swap.counterpartyAmountRaw,
        sourcePumpSwapInstruction: swap.sourcePumpSwapInstruction,
        sourcePumpSwapAccounts: swap.sourcePumpSwapAccounts,
        solInLamports: initialAmountRaw,
        slippageBps: mirrorSlippageBps,
        startedAt,
        log: (m) => console.log(m),
        onConfirmFailure: (mint, tokensRaw, solLamports, reason) => {
          const removed = isUncertainConfirmationReason(reason)
            ? false
            : unrecordBuy(mint, tokensRaw, solLamports);
          options.onFastBuyConfirmFailure?.({
            mint,
            tokenAmountRaw: tokensRaw,
            solLamports,
            reason,
            positionRemoved: removed,
            sourceSwap: swap,
          });
        },
        onConfirmSuccess: (mint, tokensRaw, solLamports, signature) => {
          options.onFastBuyConfirmSuccess?.({
            mint,
            tokenAmountRaw: tokensRaw,
            solLamports,
            signature,
            sourceSwap: swap,
          });
        },
      });
      if (pumpSwap.ok) {
        const solMeta = await getTokenMeta(WRAPPED_SOL_MINT);
        console.log(`PumpSwap direct path complete (${elapsed()})`);
        await shadowDelay();
        return applyShadowExecution({
          signature: pumpSwap.signature,
          isSimulated: pumpSwap.isSimulated,
          side: "buy",
          input: { meta: solMeta, amountRaw: Number(pumpSwap.quoteIn) },
          output: { meta: pumpSwap.mintMeta, amountRaw: Number(pumpSwap.baseOut) },
          confirmationPending: pumpSwap.confirmationPending,
        }, swap.signature);
      }
      if (!pumpSwap.recoverable) {
        throw new Error(`PumpSwap direct buy failed: ${pumpSwap.reason}`);
      }
      if (!shouldAllowJupiterAfterPumpSwapFailure(swap)) {
        throw new Error(`PumpSwap direct buy failed for pump mint; not using slow Jupiter fallback: ${pumpSwap.reason}`);
      }
      console.log(
        `PumpSwap direct path skipped: ${pumpSwap.reason}; ` +
          (jupiterFallbackEnabled ? "falling back to Jupiter." : "Jupiter fallback disabled.")
      );
      if (!mirrorGraduatedBuys) {
        throw new Error(
          "Skipping graduated buy: MIRROR_GRADUATED_BUYS is false and no direct PumpSwap route was available"
        );
      }
    } else if (shouldTryPumpSwapAfterPumpFunSkip(direct.reason, swap)) {
      console.log(
        "PumpSwap direct path disabled; " +
          (jupiterFallbackEnabled ? "falling back to Jupiter." : "Jupiter fallback disabled.")
      );
    }
  }

  // Phase 0 (sell): symmetric direct pump.fun sell path. Same accuracy/latency wins as buys —
  // no Jupiter HTTP, exact curve math for slippage protection. Falls through to Jupiter for
  // graduated tokens or non-pump.fun mints.
  if (side === "sell" && pumpfunDirectEnabled) {
    const direct = await tryPumpFunDirectSell({
      user: keypair,
      mint: swap.inputMint,
      tokensInRaw: BigInt(initialAmountRaw),
      slippageBps: mirrorSlippageBps,
      startedAt,
      log: (m) => console.log(m),
      sourceSignature: swap.signature,
      onConfirmFailure: (mint, tokensInRaw, solOutLamports, reason) => {
        options.onFastSellConfirmFailure?.({
          mint,
          tokenAmountRaw: tokensInRaw,
          solLamports: solOutLamports,
          reason,
          sourceSwap: swap,
        });
      },
      onConfirmSuccess: (mint, tokensInRaw, solOutLamports, signature) => {
        options.onFastSellConfirmSuccess?.({
          mint,
          tokenAmountRaw: tokensInRaw,
          solLamports: solOutLamports,
          signature,
          sourceSwap: swap,
        });
      },
    });
    if (direct.ok) {
      const solMeta = await getTokenMeta(WRAPPED_SOL_MINT);
      console.log(`Pump.fun direct sell complete (${elapsed()})`);
      await shadowDelay();
      return applyShadowExecution({
        signature: direct.signature,
        isSimulated: direct.isSimulated,
        side: "sell",
        input: { meta: direct.mintMeta, amountRaw: Number(direct.tokensIn) },
        output: { meta: solMeta, amountRaw: Number(direct.solOut) },
        priceImpactPct: (direct.priceImpactPct / 100).toString(),
        confirmationPending: direct.confirmationPending,
      }, swap.signature);
    }
    if (!direct.recoverable) {
      throw new Error(`Pump.fun direct sell failed: ${direct.reason}`);
    }
    console.log(
      `Pump.fun direct sell skipped: ${direct.reason}; ` +
        (jupiterFallbackEnabled ? "falling back to Jupiter." : "Jupiter fallback disabled.")
    );
    if (
      pumpSwapDirectBuyEnabled &&
      mirrorOutputMint === WRAPPED_SOL_MINT &&
      (direct.reason.toLowerCase().includes("graduated") ||
        direct.reason.toLowerCase().includes("not a pump.fun bonding-curve token") ||
        swap.inputMint.endsWith("pump"))
    ) {
      const pumpSwap = await tryPumpSwapDirectSell({
        user: keypair,
        mint: swap.inputMint,
        tokensInRaw: initialAmountRaw,
        slippageBps: mirrorSlippageBps,
        startedAt,
        log: (m) => console.log(m),
        sourceSignature: swap.signature,
        sourcePumpSwapAccounts: swap.sourcePumpSwapAccounts,
        onConfirmFailure: (mint, tokensRaw, solLamports, reason) => {
          options.onFastSellConfirmFailure?.({
            mint,
            tokenAmountRaw: tokensRaw,
            solLamports,
            reason,
            sourceSwap: swap,
          });
        },
        onConfirmSuccess: (mint, tokensRaw, solLamports, signature) => {
          options.onFastSellConfirmSuccess?.({
            mint,
            tokenAmountRaw: tokensRaw,
            solLamports,
            signature,
            sourceSwap: swap,
          });
        },
      });
      if (pumpSwap.ok) {
        const solMeta = await getTokenMeta(WRAPPED_SOL_MINT);
        console.log(`PumpSwap direct sell complete (${elapsed()})`);
        await shadowDelay();
        return applyShadowExecution({
          signature: pumpSwap.signature,
          isSimulated: pumpSwap.isSimulated,
          side: "sell",
          input: { meta: pumpSwap.mintMeta, amountRaw: Number(pumpSwap.baseIn) },
          output: { meta: solMeta, amountRaw: Number(pumpSwap.quoteOut) },
          confirmationPending: pumpSwap.confirmationPending,
        }, swap.signature);
      }
      if (!pumpSwap.recoverable) {
        throw new Error(`PumpSwap direct sell failed: ${pumpSwap.reason}`);
      }
      console.log(
        `PumpSwap direct sell skipped: ${pumpSwap.reason}; ` +
          (jupiterFallbackEnabled ? "falling back to Jupiter." : "Jupiter fallback disabled.")
      );
    }
  }

  if (
    side === "sell" &&
    mirrorOutputMint === WRAPPED_SOL_MINT &&
    Boolean(swap.sourceRaydiumCpmmAccounts?.length)
  ) {
    const raydium = await tryRaydiumCpmmDirectSell({
      user: keypair,
      mint: swap.inputMint,
      sourceSignature: swap.signature,
      sourceInputRaw: swap.amountRaw,
      sourceOutputRaw: swap.counterpartyAmountRaw,
      sourceRaydiumCpmmAccounts: swap.sourceRaydiumCpmmAccounts,
      tokensInRaw: initialAmountRaw,
      slippageBps: mirrorSlippageBps,
      startedAt,
      log: (m) => console.log(m),
      onConfirmFailure: (mint, tokensRaw, solLamports, reason) => {
        options.onFastSellConfirmFailure?.({
          mint,
          tokenAmountRaw: tokensRaw,
          solLamports,
          reason,
          sourceSwap: swap,
        });
      },
      onConfirmSuccess: (mint, tokensRaw, solLamports, signature) => {
        options.onFastSellConfirmSuccess?.({
          mint,
          tokenAmountRaw: tokensRaw,
          solLamports,
          signature,
          sourceSwap: swap,
        });
      },
    });
    if (raydium.ok) {
      console.log(`Raydium CPMM direct sell complete (${elapsed()})`);
      await shadowDelay();
      return applyShadowExecution({
        signature: raydium.signature,
        isSimulated: raydium.isSimulated,
        side: "sell",
        input: { meta: raydium.inputMeta, amountRaw: Number(raydium.inputAmount) },
        output: { meta: raydium.outputMeta, amountRaw: Number(raydium.outputAmount) },
        confirmationPending: raydium.confirmationPending,
      }, swap.signature);
    }
    console.log(`Raydium CPMM direct sell skipped: ${raydium.reason}; ${jupiterFallbackEnabled ? "falling back to Jupiter." : "Jupiter fallback disabled."}`);
  }
  const jupiterAllowed = jupiterFallbackEnabled || options.allowBagsDirectJupiter === true;
  if (!jupiterAllowed) {
    throw new Error(
      `${side} local direct execution unavailable and JUPITER_FALLBACK_ENABLED=false; not using Jupiter fallback.`
    );
  }

  // Phase 1: probe-and-shrink for buys when an impact cap is configured. Throws ImpactTooHighError
  // if even MIN_BUY can't satisfy the cap. The validated quote is reused below to skip one HTTP rt.
  let amountRaw = initialAmountRaw;
  let preFetchedQuote: QuoteResponse | undefined;
  if (side === "buy" && maxEntryPriceImpactPct != null) {
    const probed = await probeBuySizeForImpact(mirrorSwap.inputMint, mirrorSwap.outputMint, initialAmountRaw);
    amountRaw = probed.amountRaw;
    preFetchedQuote = probed.quote;
    if (amountRaw !== initialAmountRaw) {
      console.log(
        `Impact-aware sizing: ${formatQuoteAmount(initialAmountRaw, mirrorInputMint)} -> ${formatQuoteAmount(amountRaw, mirrorInputMint)}`
      );
    }
  }

  // Phase 1.5: source-fill-price drift check. If our quoted price is materially worse than the
  // source's effective fill, we're chasing — skip rather than enter at a worse price than copied.
  if (
    side === "buy" &&
    maxSourceDriftPct != null &&
    preFetchedQuote &&
    swap.inputMint === mirrorInputMint &&
    swap.counterpartyAmountRaw != null &&
    swap.counterpartyAmountRaw > 0 &&
    swap.amountRaw > 0
  ) {
    const sourcePricePerRaw = swap.amountRaw / swap.counterpartyAmountRaw; // lamports per token-raw
    const ourOutAmount = parseInt(preFetchedQuote.outAmount ?? "0", 10);
    if (ourOutAmount > 0) {
      const ourPricePerRaw = amountRaw / ourOutAmount;
      const driftPct = (ourPricePerRaw / sourcePricePerRaw - 1) * 100;
      if (Number.isFinite(driftPct) && driftPct > maxSourceDriftPct) {
        throw new SourceDriftError(mirrorSwap.outputMint, driftPct, maxSourceDriftPct);
      }
      if (Number.isFinite(driftPct)) {
        console.log(
          `Source drift: ${driftPct >= 0 ? "+" : ""}${driftPct.toFixed(1)}% (cap ${maxSourceDriftPct}%)`
        );
      }
    }
  }

  const slippageAttempts = Array.from(
    new Set([mirrorSlippageBps, mirrorRetrySlippageBps].filter((bps) => bps > 0))
  );
  const attempts: Array<{
    amountRaw: number;
    slippageBps: number;
    reason: string;
    preFetchedQuote?: QuoteResponse;
    /** Marks attempts spawned by transient-broadcast-retry — caps recursion to one. */
    isBroadcastRetry?: boolean;
    quoteMaxAccounts?: number;
    onlyDirectRoutes?: boolean;
  }> = slippageAttempts.map((slippageBps, idx) => ({
    amountRaw,
    slippageBps,
    reason: `${(slippageBps / 100).toFixed(2)}% slippage`,
    // Reuse probed quote only on the first attempt (which is at mirrorSlippageBps).
    preFetchedQuote: idx === 0 && slippageBps === mirrorSlippageBps ? preFetchedQuote : undefined,
  }));
  const minBuyForMirrorInput = mirrorInputMint === USDC_MINT ? minBuyUsdcRaw : minBuyLamports;
  if (side === "buy" && amountRaw > minBuyForMirrorInput) {
    const reducedAmountRaw = Math.max(minBuyForMirrorInput, Math.floor(amountRaw / 2));
    if (reducedAmountRaw < amountRaw) {
      attempts.push({
        amountRaw: reducedAmountRaw,
        slippageBps: mirrorRetrySlippageBps,
        reason: `reduced size ${formatQuoteAmount(reducedAmountRaw, mirrorInputMint)} + ${(mirrorRetrySlippageBps / 100).toFixed(2)}% slippage`,
      });
    }
  }
  const routeConstraintsQueued = () =>
    attempts.some((a) => a.onlyDirectRoutes || a.quoteMaxAccounts != null);
  const queueRouteConstraints = (fromAttempt: typeof attempts[number]) => {
    for (const maxAccounts of jupiterOversizeMaxAccounts) {
      attempts.push({
        amountRaw: fromAttempt.amountRaw,
        slippageBps: fromAttempt.slippageBps,
        reason: `maxAccounts=${maxAccounts} route`,
        quoteMaxAccounts: maxAccounts,
      });
    }
    attempts.push({
      amountRaw: fromAttempt.amountRaw,
      slippageBps: fromAttempt.slippageBps,
      reason: "direct Jupiter route",
      onlyDirectRoutes: true,
    });
  };
  let lastSlippageError: unknown;

  for (let i = 0; i < attempts.length; i++) {
    const attempt = attempts[i]!;
    const isLastAttempt = i === attempts.length - 1;
    let activeStage: "quote" | "swap-response" | "build" | "broadcast" | "confirm" | "metadata" = "quote";
    let activeStageStartedAt = Date.now();

    try {
      let quote: QuoteResponse;
      if (attempt.preFetchedQuote) {
        activeStage = "quote";
        activeStageStartedAt = Date.now();
        quote = attempt.preFetchedQuote;
        console.log(
          `Reusing probed quote for ${formatQuoteAmount(attempt.amountRaw, mirrorInputMint)} (${attempt.reason}); out=${quote.outAmount} (${elapsed()})`
        );
      } else {
        console.log(
          `Getting quote for ${formatQuoteAmount(attempt.amountRaw, mirrorInputMint)} (${attempt.reason})...`
        );
        activeStage = "quote";
        const quoteStartedAt = Date.now();
        activeStageStartedAt = quoteStartedAt;
        quote = await getQuote(
          mirrorSwap.inputMint,
          mirrorSwap.outputMint,
          attempt.amountRaw,
          attempt.slippageBps,
          {
            maxAccounts: attempt.quoteMaxAccounts,
            onlyDirectRoutes: attempt.onlyDirectRoutes,
          }
        );
        recordExecutionStage({
          stage: "quote",
          route: `jupiter-${side}`,
          side,
          mint: side === "buy" ? mirrorSwap.outputMint : mirrorSwap.inputMint,
          sourceSignature: swap.signature,
          elapsedMs: Date.now() - quoteStartedAt,
          success: true,
          details: {
            attempt: i + 1,
            reason: attempt.reason,
            slippageBps: attempt.slippageBps,
            amountRaw: attempt.amountRaw,
            maxAccounts: attempt.quoteMaxAccounts,
            onlyDirectRoutes: attempt.onlyDirectRoutes,
            outAmount: quote.outAmount,
            priceImpactPct: quote.priceImpactPct,
          },
        });
        console.log(`Quote received: ${quote.outAmount} out amount (${elapsed()})`);
      }
      if (side === "buy") {
        assertEntryImpactWithinCap(mirrorSwap.outputMint, quote);
      }

      const inAmountRaw = parseInt(quote.inAmount ?? String(attempt.amountRaw), 10);
      const outAmountRaw = parseInt(quote.outAmount ?? "0", 10);

      console.log("Getting swap response...");
      activeStage = "swap-response";
      const swapResponseStartedAt = Date.now();
      activeStageStartedAt = swapResponseStartedAt;
      const swapResponse = await getSwap(quote, keypair.publicKey.toBase58());
      recordExecutionStage({
        stage: "swap-response",
        route: `jupiter-${side}`,
        side,
        mint: side === "buy" ? mirrorSwap.outputMint : mirrorSwap.inputMint,
        sourceSignature: swap.signature,
        elapsedMs: Date.now() - swapResponseStartedAt,
        success: true,
        details: { attempt: i + 1, reason: attempt.reason },
      });
      console.log(`Swap response received (${elapsed()})`);

      console.log("Creating transaction...");
      const jupiterRoute = `jupiter-${side}`;
      activeStage = "build";
      const buildStartedAt = Date.now();
      activeStageStartedAt = buildStartedAt;
      const transaction = await createSenderTransactionFromSwapResponse(
        swapResponse as { swapTransaction: string; [key: string]: unknown },
        keypair,
        { route: jupiterRoute }
      );
      recordExecutionStage({
        stage: "build",
        route: jupiterRoute,
        side,
        mint: side === "buy" ? mirrorSwap.outputMint : mirrorSwap.inputMint,
        sourceSignature: swap.signature,
        elapsedMs: Date.now() - buildStartedAt,
        success: true,
        details: {
          attempt: i + 1,
          reason: attempt.reason,
          packetBytes: transaction.serialize().length,
        },
      });
      console.log(`Transaction created (${elapsed()})`);

      if (simulationMode) {
        await shadowSimulateTransaction(transaction, `${side} ${mirrorSwap.outputMint.slice(0, 8)}...`);
        console.log("Getting token metadata...");
        const [inputMeta, outputMeta] = await Promise.all([
          getTokenMeta(mirrorSwap.inputMint),
          getTokenMeta(mirrorSwap.outputMint),
        ]);
        console.log("Token metadata received");
      console.log("Simulation mode - returning simulated result");
        await shadowDelay();
        return applyShadowExecution({
          signature: `sim-${swap.signature}-${Date.now()}`,
          isSimulated: true,
          side,
          input: { meta: inputMeta, amountRaw: inAmountRaw },
          output: { meta: outputMeta, amountRaw: outAmountRaw },
          priceImpactPct: quote.priceImpactPct,
          costBasisLamports: side === "buy" ? estimateCostBasisLamports(mirrorSwap.inputMint, inAmountRaw) : undefined,
        }, swap.signature);
      }

      console.log("Broadcasting transaction...");
      activeStage = "broadcast";
      activeStageStartedAt = Date.now();
      const signature = await broadcastTransactionWithSender(transaction, {
        preferHelius: attempt.isBroadcastRetry === true,
        route: jupiterRoute,
        side,
        mint: side === "buy" ? mirrorSwap.outputMint : mirrorSwap.inputMint,
        sourceSignature: swap.signature,
      });
      console.log(`Transaction broadcast: ${signature} (${elapsed()})`);

      const buyCostBasisLamports =
        side === "buy" ? estimateCostBasisLamports(mirrorSwap.inputMint, inAmountRaw) : undefined;

      if (fastMode) {
        // Fast mode returns after broadcast; dropped buys/sells are cleaned up by the background confirmer.
        confirmSignature(signature)
          .then(() => {
            if (side === "buy") {
              options.onFastBuyConfirmSuccess?.({
                mint: mirrorSwap.outputMint,
                tokenAmountRaw: outAmountRaw,
                solLamports: buyCostBasisLamports ?? inAmountRaw,
                signature,
                sourceSwap: mirrorSwap,
              });
              return;
            }
            if (side === "sell") {
              options.onFastSellConfirmSuccess?.({
                mint: mirrorSwap.inputMint,
                tokenAmountRaw: inAmountRaw,
                solLamports: outAmountRaw,
                signature,
                sourceSwap: mirrorSwap,
              });
            }
          })
          .catch((err) => {
            const reason = err instanceof Error ? err.message : String(err);
            if (side === "buy") {
              const removed = isConfirmationTimeoutError(err)
                ? false
                : unrecordBuy(mirrorSwap.outputMint, outAmountRaw, buyCostBasisLamports ?? inAmountRaw);
              options.onFastBuyConfirmFailure?.({
                mint: mirrorSwap.outputMint,
                tokenAmountRaw: outAmountRaw,
                solLamports: buyCostBasisLamports ?? inAmountRaw,
                reason,
                positionRemoved: removed,
                sourceSwap: mirrorSwap,
              });
              return;
            }
            if (side === "sell") {
              options.onFastSellConfirmFailure?.({
                mint: mirrorSwap.inputMint,
                tokenAmountRaw: inAmountRaw,
                solLamports: outAmountRaw,
                reason,
                sourceSwap: mirrorSwap,
              });
            }
          });
      } else {
        console.log("Confirming transaction...");
        activeStage = "confirm";
        const confirmStartedAt = Date.now();
        activeStageStartedAt = confirmStartedAt;
        await confirmSignature(signature);
        recordExecutionStage({
          stage: "confirm",
          route: jupiterRoute,
          side,
          mint: side === "buy" ? mirrorSwap.outputMint : mirrorSwap.inputMint,
          sourceSignature: swap.signature,
          signature,
          elapsedMs: Date.now() - confirmStartedAt,
          success: true,
          details: { attempt: i + 1, reason: attempt.reason },
        });
        console.log(`Transaction confirmed (${elapsed()})`);
      }

      console.log("Getting token metadata...");
      activeStage = "metadata";
      const metadataStartedAt = Date.now();
      activeStageStartedAt = metadataStartedAt;
      const [inputMeta, outputMeta] = await Promise.all([
        getTokenMeta(mirrorSwap.inputMint),
        getTokenMeta(mirrorSwap.outputMint),
      ]);
      recordExecutionStage({
        stage: "metadata",
        route: jupiterRoute,
        side,
        mint: side === "buy" ? mirrorSwap.outputMint : mirrorSwap.inputMint,
        sourceSignature: swap.signature,
        signature,
        elapsedMs: Date.now() - metadataStartedAt,
        success: true,
      });
      console.log("Token metadata received");

      return {
        signature,
        isSimulated: false,
        side,
        input: { meta: inputMeta, amountRaw: inAmountRaw },
        output: { meta: outputMeta, amountRaw: outAmountRaw },
        priceImpactPct: quote.priceImpactPct,
        costBasisLamports: buyCostBasisLamports,
        confirmationPending: fastMode,
      };
    } catch (err) {
      recordExecutionStage({
        stage: activeStage,
        route: `jupiter-${side}`,
        side,
        mint: side === "buy" ? mirrorSwap.outputMint : mirrorSwap.inputMint,
        sourceSignature: swap.signature,
        elapsedMs: Date.now() - activeStageStartedAt,
        success: false,
        error: err instanceof Error ? err.message : String(err),
        details: {
          attempt: i + 1,
          reason: attempt.reason,
          slippageBps: attempt.slippageBps,
          amountRaw: attempt.amountRaw,
          maxAccounts: attempt.quoteMaxAccounts,
          onlyDirectRoutes: attempt.onlyDirectRoutes,
        },
      });
      // Slippage-driven retry: walk down the ladder.
      if (isSlippageFailure(err) && !isLastAttempt) {
        lastSlippageError = err;
        console.warn(
          `Slippage exceeded on ${attempt.reason}; retrying with ${attempts[i + 1]!.reason}...`
        );
        continue;
      }
      if (isPacketSizeFailure(err)) {
        const msg = err instanceof Error ? err.message : String(err);
        if (attempt.quoteMaxAccounts != null || attempt.onlyDirectRoutes) {
          if (!isLastAttempt) {
            console.warn(`Constrained Jupiter route still too large (${msg}); trying next route constraint...`);
            continue;
          }
        } else if (!routeConstraintsQueued()) {
          console.warn(`Jupiter route too large (${msg}); retrying with constrained routes...`);
          queueRouteConstraints(attempt);
          continue;
        } else if (!isLastAttempt) {
          console.warn(`Jupiter route still too large (${msg}); trying queued constrained route...`);
          continue;
        }
      }
      if (isNoRouteFailure(err) && (attempt.quoteMaxAccounts != null || attempt.onlyDirectRoutes) && !isLastAttempt) {
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`Constrained Jupiter route unavailable (${msg}); trying next route constraint...`);
        continue;
      }
      // Transient broadcast retry: re-quote from scratch (fresh blockhash). Bounded to ONE
      // additional attempt past the slippage ladder so we don't add unbounded latency.
      if (isTransientBroadcastFailure(err) && !attempt.isBroadcastRetry) {
        const retrySlippage = side === "sell" ? mirrorRetrySlippageBps : attempt.slippageBps;
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(
          `Transient broadcast failure (${msg}); retrying with fresh quote at ${(retrySlippage / 100).toFixed(2)}% slippage...`
        );
        attempts.push({
          amountRaw: attempt.amountRaw,
          slippageBps: retrySlippage,
          reason: `transient-retry @ ${(retrySlippage / 100).toFixed(2)}% slippage`,
          isBroadcastRetry: true,
        });
        continue;
      }
      throw err;
    }
  }

  throw lastSlippageError instanceof Error
    ? lastSlippageError
    : new Error("Swap failed after slippage retry");
}

