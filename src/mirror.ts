import { Keypair } from "@solana/web3.js";
import {
  minBuyLamports,
  maxBuyLamports,
  simulationMode,
  fastMode,
  mirrorSlippageBps,
  mirrorRetrySlippageBps,
  maxEntryPriceImpactPct,
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

export class PriceImpactTooHighError extends Error {
  readonly mint: string;
  readonly priceImpactPct: number;
  readonly maxPriceImpactPct: number;

  constructor(mint: string, priceImpactPct: number, maxPriceImpactPct: number) {
    super(
      `Entry price impact ${priceImpactPct.toFixed(2)}% exceeds max ${maxPriceImpactPct.toFixed(2)}%`
    );
    this.mint = mint;
    this.priceImpactPct = priceImpactPct;
    this.maxPriceImpactPct = maxPriceImpactPct;
    this.name = "PriceImpactTooHighError";
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
    const amountRaw = simulationMode
      ? getPosition(swap.inputMint)?.heldRaw ?? 0
      : await getTokenBalance(keypair.publicKey, swap.inputMint);

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
  let lastSlippageError: unknown;

  for (let i = 0; i < attempts.length; i++) {
    const attempt = attempts[i]!;
    const isLastAttempt = i === attempts.length - 1;

    try {
      console.log(
        `Getting quote for ${attempt.amountRaw / 1_000_000_000} SOL (${attempt.reason})...`
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
      const priceImpactPct = quote.priceImpactPct ? parseFloat(quote.priceImpactPct) * 100 : null;
      if (
        side === "buy" &&
        maxEntryPriceImpactPct != null &&
        priceImpactPct != null &&
        Number.isFinite(priceImpactPct) &&
        priceImpactPct > maxEntryPriceImpactPct
      ) {
        throw new PriceImpactTooHighError(
          swap.outputMint,
          priceImpactPct,
          maxEntryPriceImpactPct
        );
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
      if (!isSlippageFailure(err) || isLastAttempt) {
        throw err;
      }
      lastSlippageError = err;
      console.warn(
        `Slippage exceeded on ${attempt.reason}; retrying with ${attempts[i + 1]!.reason}...`
      );
    }
  }

  throw lastSlippageError instanceof Error
    ? lastSlippageError
    : new Error("Swap failed after slippage retry");
}
