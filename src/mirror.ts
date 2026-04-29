import { Keypair } from "@solana/web3.js";
import {
  minBuyLamports,
  maxBuyLamports,
  dryRunMode,
  simulationMode,
  fastMode,
  mirrorSlippageBps,
  mirrorRetrySlippageBps,
  copySellFullBalance,
  minSellOutputLamports,
} from "./config.js";
import { getQuote, getSwap } from "./jupiter.js";
import {
  createSenderTransactionFromSwapResponse,
  broadcastTransactionWithSender,
} from "./sender.js";
import { confirmSignature } from "./confirm.js";
import { getTokenBalance } from "./balance.js";
import { getTokenMeta, WRAPPED_SOL_MINT, type TokenMeta } from "./tokenRegistry.js";
import { getPosition } from "./pnl.js";
import type { DetectedSwap } from "./types.js";

const COPY_SELL_BALANCE_BUFFER_BPS = 20;

function bufferedSellAmount(heldRaw: number): number {
  return Math.max(
    1,
    Math.floor((heldRaw * (10_000 - COPY_SELL_BALANCE_BUFFER_BPS)) / 10_000)
  );
}

export type Side = "buy" | "sell" | "swap";

export type MirrorResult = {
  signature: string;
  isSimulated: boolean;
  isDryRun?: boolean;
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

export class DustSellError extends Error {
  readonly mint: string;
  readonly outLamports: number;
  readonly minLamports: number;

  constructor(mint: string, outLamports: number, minLamports: number) {
    super(
      `Skipping sell for ${mint}: quote output ${outLamports} lamports is below minimum ${minLamports} lamports`
    );
    this.mint = mint;
    this.outLamports = outLamports;
    this.minLamports = minLamports;
    this.name = "DustSellError";
  }
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

function isSellRetryableFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return (
    msg.includes("6024") ||
    msg.includes("InsufficientFunds") ||
    msg.toLowerCase().includes("insufficient funds") ||
    msg.toLowerCase().includes("insufficient liquidity")
  );
}

function isTransientUpstreamFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  const lower = msg.toLowerCase();
  return (
    lower.includes("returned non-json") ||
    lower.includes("returned invalid json") ||
    lower.includes("fetch failed") ||
    lower.includes("network") ||
    msg.includes("HTTP 429") ||
    msg.includes("HTTP 500") ||
    msg.includes("HTTP 502") ||
    msg.includes("HTTP 503") ||
    msg.includes("HTTP 504")
  );
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
        `Trade skipped: ${(swap.amountRaw / 1_000_000_000).toFixed(4)} SOL < min: ${minBuyLamports / 1_000_000_000} SOL`
      );
      throw new Error(
        `Swap amount ${swap.amountRaw} lamports below min ${minBuyLamports}, skipped`
      );
    }

    return amountRaw;
  }

  if (side === "sell") {
    const trackedHeldRaw = getPosition(swap.inputMint)?.heldRaw ?? 0;
    const heldRaw = simulationMode || dryRunMode
      ? trackedHeldRaw
      : await getLiveSellAmount(keypair, swap.inputMint, trackedHeldRaw);
    const amountRaw = copySellFullBalance ? heldRaw : bufferedSellAmount(heldRaw);

    if (heldRaw <= 0 || amountRaw <= 0) {
      throw new NoPositionError(swap.inputMint);
    }

    if (!copySellFullBalance && amountRaw < heldRaw) {
      console.log(
        `Copy sell amount buffered: selling ${(100 - COPY_SELL_BALANCE_BUFFER_BPS / 100).toFixed(2)}% of live balance to avoid dust/rent mismatches.`
      );
    } else if (copySellFullBalance) {
      console.log(
        "Copy sell full-balance mode: trying 100% first; buffered retries will be used if preflight fails."
      );
    }

    return amountRaw;
  }

  throw new Error(`Unsupported token-to-token swap (input ${swap.inputMint})`);
}

async function getLiveSellAmount(
  keypair: Keypair,
  mint: string,
  trackedHeldRaw: number
): Promise<number> {
  try {
    const liveHeldRaw = await getTokenBalance(keypair.publicKey, mint);
    if (liveHeldRaw > 0) return liveHeldRaw;
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(
      `Live token balance lookup failed for ${mint.slice(0, 8)}...; ${
        trackedHeldRaw > 0 ? "falling back to tracked position" : "no tracked fallback available"
      }: ${msg}`
    );
  }

  if (trackedHeldRaw > 0) {
    console.warn(
      `Live token balance lookup returned 0 for ${mint.slice(0, 8)}...; attempting tracked position amount.`
    );
  }
  return trackedHeldRaw;
}

export async function mirrorSwap(
  swap: DetectedSwap,
  keypair: Keypair
): Promise<MirrorResult> {
  const startedAt = Date.now();
  const elapsed = () => `${Date.now() - startedAt}ms`;
  const side = classifySide(swap.inputMint, swap.outputMint);
  const amountRaw = await getMirrorAmount(side, swap, keypair);
  const slippageAttempts = Array.from(
    new Set([mirrorSlippageBps, mirrorRetrySlippageBps].filter((bps) => bps > 0))
  );
  const attempts = slippageAttempts.map((slippageBps) => ({
    amountRaw,
    slippageBps,
    reason: `${(slippageBps / 100).toFixed(2)}% slippage`,
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
  if (side === "sell" && amountRaw > 1) {
    const scales = copySellFullBalance
      ? [(10_000 - COPY_SELL_BALANCE_BUFFER_BPS) / 10_000, 0.99, 0.95]
      : [0.99, 0.95];
    for (const scale of scales) {
      const reducedAmountRaw = Math.max(1, Math.floor(amountRaw * scale));
      if (reducedAmountRaw < amountRaw) {
        attempts.push({
          amountRaw: reducedAmountRaw,
          slippageBps: mirrorRetrySlippageBps,
          reason: `reduced sell size ${(scale * 100).toFixed(0)}% + ${(mirrorRetrySlippageBps / 100).toFixed(2)}% slippage`,
        });
      }
    }
  }
  let lastSlippageError: unknown;

  for (let i = 0; i < attempts.length; i++) {
    const attempt = attempts[i]!;
    const isLastAttempt = i === attempts.length - 1;

    try {
      const amountLabel =
        side === "buy"
          ? `${attempt.amountRaw / 1_000_000_000} SOL`
          : `${attempt.amountRaw} raw tokens`;
      console.log(
        `Getting quote for ${amountLabel} (${attempt.reason})...`
      );
      const quote = await getQuote(
        swap.inputMint,
        swap.outputMint,
        attempt.amountRaw,
        attempt.slippageBps
      );
      console.log(`Quote received: ${quote.outAmount} out amount (${elapsed()})`);

      const inAmountRaw = parseInt(quote.inAmount ?? String(attempt.amountRaw), 10);
      const outAmountRaw = parseInt(quote.outAmount ?? "0", 10);
      if (side === "sell" && minSellOutputLamports > 0 && outAmountRaw < minSellOutputLamports) {
        throw new DustSellError(swap.inputMint, outAmountRaw, minSellOutputLamports);
      }

      if (dryRunMode) {
        console.log("Dry run mode - quote received; skipping swap response, transaction creation, broadcast, and confirmation.");
        console.log("Getting token metadata...");
        const [inputMeta, outputMeta] = await Promise.all([
          getTokenMeta(swap.inputMint),
          getTokenMeta(swap.outputMint),
        ]);
        console.log("Token metadata received");
        return {
          signature: `dry-${swap.signature}-${Date.now()}`,
          isSimulated: true,
          isDryRun: true,
          side,
          input: { meta: inputMeta, amountRaw: inAmountRaw },
          output: { meta: outputMeta, amountRaw: outAmountRaw },
          priceImpactPct: quote.priceImpactPct,
        };
      }

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
          "Fast mode is enabled, but confirmation is still required before recording live PnL."
        );
      }
      console.log("Confirming transaction...");
      await confirmSignature(signature);
      console.log(`Transaction confirmed (${elapsed()})`);

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
      const retryable =
        isSlippageFailure(err) ||
        isTransientUpstreamFailure(err) ||
        (side === "sell" && isSellRetryableFailure(err));
      if (!retryable || isLastAttempt) {
        throw err;
      }
      lastSlippageError = err;
      console.warn(
        `Swap failed on ${attempt.reason}; retrying with ${attempts[i + 1]!.reason}...`
      );
    }
  }

  throw lastSlippageError instanceof Error
    ? lastSlippageError
    : new Error("Swap failed after slippage retry");
}
