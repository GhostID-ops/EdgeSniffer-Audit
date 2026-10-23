import { PublicKey } from "@solana/web3.js";
import {
  pumpSwapPrewarmEnabled,
  pumpSwapPrewarmUseSourceRatio,
  prewarmWallets,
} from "./config.js";
import { recordExecutionTelemetrySync } from "./executionTelemetry.js";
import { prewarmPumpSwapBuyAccounts } from "./pumpswap/route.js";
import type { DetectedSwap } from "./types.js";
import { USDC_MINT, WRAPPED_SOL_MINT } from "./tokenRegistry.js";

export function isPumpSwapPrewarmCandidateSwap(swap: DetectedSwap): boolean {
  return (
    (swap.inputMint === WRAPPED_SOL_MINT || swap.inputMint === USDC_MINT) &&
    swap.outputMint !== WRAPPED_SOL_MINT &&
    swap.outputMint !== USDC_MINT &&
    swap.outputMint.endsWith("pump")
  );
}

function shouldPrewarmSignal(swap: DetectedSwap): boolean {
  if (!pumpSwapPrewarmEnabled || prewarmWallets.length === 0) return false;
  return isPumpSwapPrewarmCandidateSwap(swap);
}

export async function handlePumpSwapPrewarmSignal(
  swap: DetectedSwap,
  user: PublicKey,
  log: (msg: string) => void = console.log
): Promise<void> {
  if (!shouldPrewarmSignal(swap)) return;

  const startedAt = Date.now();
  try {
    const result = await prewarmPumpSwapBuyAccounts({
      mint: swap.outputMint,
      user,
      sourceSignature: swap.signature,
      quoteMint: swap.inputMint,
    });
    recordExecutionTelemetrySync({
      kind: "prewarm",
      route: "pumpswap-direct-buy",
      side: "buy",
      mint: swap.outputMint,
      sourceSignature: swap.signature,
      wallet: swap.wallet,
      detector: swap.detector,
      elapsedMs: result.elapsedMs,
      success: result.planCached && result.poolResolved,
      details: {
        pool: result.pool,
        accountPlan: result.accountPlan,
        accountCount: result.accountCount,
        planCached: result.planCached,
        poolResolved: result.poolResolved,
        baseTokenProgram: result.baseTokenProgram,
        skippedReason: result.skippedReason,
        useSourceRatioQuote: pumpSwapPrewarmUseSourceRatio,
      },
    });
    if (result.planCached) {
      log(
        `PumpSwap prewarm cached ${swap.outputMint.slice(0, 8)}... ` +
          `from ${swap.wallet.slice(0, 4)}...${swap.wallet.slice(-4)} ` +
          `(${result.elapsedMs}ms, pool=${result.poolResolved ? "yes" : "no"}).`
      );
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    recordExecutionTelemetrySync({
      kind: "prewarm",
      route: "pumpswap-direct-buy",
      side: "buy",
      mint: swap.outputMint,
      sourceSignature: swap.signature,
      wallet: swap.wallet,
      detector: swap.detector,
      elapsedMs: Date.now() - startedAt,
      success: false,
      error: msg,
    });
  }
}
