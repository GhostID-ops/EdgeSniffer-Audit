import { Keypair } from "@solana/web3.js";
import {
  minBuyLamports,
  maxBuyLamports,
  simulationMode,
  fastMode,
  mirrorSlippageBps,
  mirrorRetrySlippageBps,
  maxEntryPriceImpactPct,
  maxImpactReduceAttempts,
  maxSourceDriftPct,
  pumpfunDirectEnabled,
} from "./config.js";
import { getQuote, getSwap, type QuoteResponse } from "./jupiter.js";
import {
  createSenderTransactionFromSwapResponse,
  broadcastTransactionWithSender,
} from "./sender.js";
import { confirmSignature } from "./confirm.js";
import { getTokenBalance, getTokenBalanceFast } from "./balance.js";
import { getTokenMeta, WRAPPED_SOL_MINT, type TokenMeta } from "./tokenRegistry.js";
import { getPosition } from "./pnl.js";
import type { DetectedSwap } from "./types.js";
import { tryPumpFunDirectBuy, tryPumpFunDirectSell } from "./pumpfun/route.js";

export type Side = "buy" | "sell" | "swap";

export type MirrorResult = {
  signature: string;
  isSimulated: boolean;
  side: Side;
  input: { meta: TokenMeta; amountRaw: number };
  output: { meta: TokenMeta; amountRaw: number };
  /** Jupiter's reported price impact as a decimal string (e.g. "0.0234" = 2.34%). */
  priceImpactPct?: string;
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

  for (let attempt = 0; attempt <= maxImpactReduceAttempts; attempt++) {
    const quote = await getQuote(inputMint, outputMint, probed, mirrorSlippageBps);
    const impactPct = quote.priceImpactPct ? parseFloat(quote.priceImpactPct) * 100 : 0;
    lastQuote = quote;
    lastImpactPct = impactPct;

    // Jupiter sometimes returns NaN/undefined for synthetic or no-route quotes — treat as pass.
    if (!Number.isFinite(impactPct) || maxEntryPriceImpactPct == null || impactPct <= maxEntryPriceImpactPct) {
      return { amountRaw: probed, quote };
    }

    console.warn(
      `Entry impact ${impactPct.toFixed(1)}% > cap ${maxEntryPriceImpactPct.toFixed(1)}% at ${(probed / 1e9).toFixed(4)} SOL`
    );

    if (probed <= minBuyLamports || attempt === maxImpactReduceAttempts) break;

    // Reduce size proportional to overshoot, but never less than 0.25× and never more than 0.6× of current.
    // This avoids both "too small to matter" and "barely smaller, will probably fail again."
    const targetFraction = Math.max(0.25, Math.min(0.6, maxEntryPriceImpactPct / impactPct));
    const next = Math.max(minBuyLamports, Math.floor(probed * targetFraction));
    if (next >= probed) break;
    console.log(
      `Reducing buy size: ${(probed / 1e9).toFixed(4)} → ${(next / 1e9).toFixed(4)} SOL`
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
  if (inputMint === WRAPPED_SOL_MINT) return "buy";
  if (outputMint === WRAPPED_SOL_MINT) return "sell";
  return "swap";
}

function clampBuy(amountRaw: number): number {
  return Math.max(minBuyLamports, Math.min(maxBuyLamports, amountRaw));
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

function confirmInBackground(signature: string): void {
  void confirmSignature(signature)
    .then(() => console.log(`Transaction confirmed in background: ${signature}`))
    .catch((err) => {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Background confirmation failed for ${signature}: ${msg}`);
    });
}

async function getMirrorAmount(
  side: Side,
  swap: DetectedSwap,
  keypair: Keypair
): Promise<number> {
  if (side === "buy") {
    const amountRaw = clampBuy(swap.amountRaw);

    if (swap.amountRaw > maxBuyLamports) {
      console.log(
        `Trade capped: ${(swap.amountRaw / 1_000_000_000).toFixed(4)} SOL -> ${(amountRaw / 1_000_000_000).toFixed(4)} SOL (max: ${maxBuyLamports / 1_000_000_000})`
      );
    } else if (swap.amountRaw < minBuyLamports) {
      console.log(
        `Trade raised to min buy: ${(swap.amountRaw / 1_000_000_000).toFixed(4)} SOL -> ${(amountRaw / 1_000_000_000).toFixed(4)} SOL`
      );
    }

    return amountRaw;
  }

  if (side === "sell") {
    let amountRaw: number;
    if (simulationMode) {
      amountRaw = getPosition(swap.inputMint)?.heldRaw ?? 0;
    } else {
      // Fast path: direct ATA read. Falls back to scan-all on null (e.g., unusual token program).
      const fast = await getTokenBalanceFast(keypair.publicKey, swap.inputMint);
      amountRaw = fast ?? (await getTokenBalance(keypair.publicKey, swap.inputMint));
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
  keypair: Keypair
): Promise<MirrorResult> {
  const startedAt = Date.now();
  const elapsed = () => `${Date.now() - startedAt}ms`;
  const side = classifySide(swap.inputMint, swap.outputMint);
  const initialAmountRaw = await getMirrorAmount(side, swap, keypair);

  // Phase 0: Direct pump.fun bonding-curve route. Bypasses Jupiter entirely for pre-graduation
  // pump.fun tokens — solves the "Jupiter says 100% impact" indexing-lag problem and shaves
  // 150-300ms off the entry path. Falls through to Jupiter on any non-recoverable signal.
  if (side === "buy" && pumpfunDirectEnabled) {
    const direct = await tryPumpFunDirectBuy({
      user: keypair,
      mint: swap.outputMint,
      solInLamports: initialAmountRaw,
      slippageBps: mirrorSlippageBps,
      maxImpactPct: maxEntryPriceImpactPct,
      minSolLamports: minBuyLamports,
      startedAt,
      log: (m) => console.log(m),
    });
    if (direct.ok) {
      const solMeta = await getTokenMeta(WRAPPED_SOL_MINT);
      console.log(`Pump.fun direct path complete (${elapsed()})`);
      return {
        signature: direct.signature,
        isSimulated: direct.isSimulated,
        side: "buy",
        input: { meta: solMeta, amountRaw: Number(direct.solIn) },
        output: { meta: direct.mintMeta, amountRaw: Number(direct.tokensOut) },
        priceImpactPct: (direct.priceImpactPct / 100).toString(),
      };
    }
    if (!direct.recoverable) {
      // Hard fail (broadcast error, curve too thin) — surface it; don't try Jupiter for this trade.
      throw new Error(`Pump.fun direct buy failed: ${direct.reason}`);
    }
    // Recoverable: graduated token, not on curve, etc. — fall through to Jupiter below.
    console.log(`Pump.fun direct path skipped: ${direct.reason}; falling back to Jupiter.`);
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
    });
    if (direct.ok) {
      const solMeta = await getTokenMeta(WRAPPED_SOL_MINT);
      console.log(`Pump.fun direct sell complete (${elapsed()})`);
      return {
        signature: direct.signature,
        isSimulated: direct.isSimulated,
        side: "sell",
        input: { meta: direct.mintMeta, amountRaw: Number(direct.tokensIn) },
        output: { meta: solMeta, amountRaw: Number(direct.solOut) },
        priceImpactPct: (direct.priceImpactPct / 100).toString(),
      };
    }
    if (!direct.recoverable) {
      throw new Error(`Pump.fun direct sell failed: ${direct.reason}`);
    }
    console.log(`Pump.fun direct sell skipped: ${direct.reason}; falling back to Jupiter.`);
  }

  // Phase 1: probe-and-shrink for buys when an impact cap is configured. Throws ImpactTooHighError
  // if even MIN_BUY can't satisfy the cap. The validated quote is reused below to skip one HTTP rt.
  let amountRaw = initialAmountRaw;
  let preFetchedQuote: QuoteResponse | undefined;
  if (side === "buy" && maxEntryPriceImpactPct != null) {
    const probed = await probeBuySizeForImpact(swap.inputMint, swap.outputMint, initialAmountRaw);
    amountRaw = probed.amountRaw;
    preFetchedQuote = probed.quote;
    if (amountRaw !== initialAmountRaw) {
      console.log(
        `Impact-aware sizing: ${(initialAmountRaw / 1e9).toFixed(4)} → ${(amountRaw / 1e9).toFixed(4)} SOL`
      );
    }
  }

  // Phase 1.5: source-fill-price drift check. If our quoted price is materially worse than the
  // source's effective fill, we're chasing — skip rather than enter at a worse price than copied.
  if (
    side === "buy" &&
    maxSourceDriftPct != null &&
    preFetchedQuote &&
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
        throw new SourceDriftError(swap.outputMint, driftPct, maxSourceDriftPct);
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
  }> = slippageAttempts.map((slippageBps, idx) => ({
    amountRaw,
    slippageBps,
    reason: `${(slippageBps / 100).toFixed(2)}% slippage`,
    // Reuse probed quote only on the first attempt (which is at mirrorSlippageBps).
    preFetchedQuote: idx === 0 && slippageBps === mirrorSlippageBps ? preFetchedQuote : undefined,
  }));
  if (side === "buy" && amountRaw > minBuyLamports) {
    const reducedAmountRaw = Math.max(minBuyLamports, Math.floor(amountRaw / 2));
    if (reducedAmountRaw < amountRaw) {
      attempts.push({
        amountRaw: reducedAmountRaw,
        slippageBps: mirrorRetrySlippageBps,
        reason: `reduced size ${(reducedAmountRaw / 1_000_000_000).toFixed(4)} SOL + ${(mirrorRetrySlippageBps / 100).toFixed(2)}% slippage`,
      });
    }
  }
  let lastSlippageError: unknown;

  for (let i = 0; i < attempts.length; i++) {
    const attempt = attempts[i]!;
    const isLastAttempt = i === attempts.length - 1;

    try {
      let quote: QuoteResponse;
      if (attempt.preFetchedQuote) {
        quote = attempt.preFetchedQuote;
        console.log(
          `Reusing probed quote for ${attempt.amountRaw / 1_000_000_000} SOL (${attempt.reason}); out=${quote.outAmount} (${elapsed()})`
        );
      } else {
        console.log(
          `Getting quote for ${attempt.amountRaw / 1_000_000_000} SOL (${attempt.reason})...`
        );
        quote = await getQuote(
          swap.inputMint,
          swap.outputMint,
          attempt.amountRaw,
          attempt.slippageBps
        );
        console.log(`Quote received: ${quote.outAmount} out amount (${elapsed()})`);
      }

      const inAmountRaw = parseInt(quote.inAmount ?? String(attempt.amountRaw), 10);
      const outAmountRaw = parseInt(quote.outAmount ?? "0", 10);

      console.log("Getting swap response...");
      const swapResponse = await getSwap(quote, keypair.publicKey.toBase58());
      console.log(`Swap response received (${elapsed()})`);

      console.log("Creating transaction...");
      const transaction = await createSenderTransactionFromSwapResponse(
        swapResponse as { swapTransaction: string; [key: string]: unknown },
        keypair
      );
      console.log(`Transaction created (${elapsed()})`);

      if (simulationMode) {
        console.log("Getting token metadata...");
        const [inputMeta, outputMeta] = await Promise.all([
          getTokenMeta(swap.inputMint),
          getTokenMeta(swap.outputMint),
        ]);
        console.log("Token metadata received");
        console.log("Simulation mode - returning simulated result");
        return {
          signature: `sim-${swap.signature}-${Date.now()}`,
          isSimulated: true,
          side,
          input: { meta: inputMeta, amountRaw: inAmountRaw },
          output: { meta: outputMeta, amountRaw: outAmountRaw },
          priceImpactPct: quote.priceImpactPct,
        };
      }

      console.log("Broadcasting transaction...");
      const signature = await broadcastTransactionWithSender(transaction);
      console.log(`Transaction broadcast: ${signature} (${elapsed()})`);

      if (fastMode) {
        console.log(
          "Fast mode enabled: recording immediately after broadcast; confirmation continues in background."
        );
        confirmInBackground(signature);
      } else {
        console.log("Confirming transaction...");
        await confirmSignature(signature);
        console.log(`Transaction confirmed (${elapsed()})`);
      }

      console.log("Getting token metadata...");
      const [inputMeta, outputMeta] = await Promise.all([
        getTokenMeta(swap.inputMint),
        getTokenMeta(swap.outputMint),
      ]);
      console.log("Token metadata received");

      return {
        signature,
        isSimulated: false,
        side,
        input: { meta: inputMeta, amountRaw: inAmountRaw },
        output: { meta: outputMeta, amountRaw: outAmountRaw },
        priceImpactPct: quote.priceImpactPct,
      };
    } catch (err) {
      // Slippage-driven retry: walk down the ladder.
      if (isSlippageFailure(err) && !isLastAttempt) {
        lastSlippageError = err;
        console.warn(
          `Slippage exceeded on ${attempt.reason}; retrying with ${attempts[i + 1]!.reason}...`
        );
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
