import { LAMPORTS_PER_SOL, type VersionedTransaction } from "@solana/web3.js";
import {
  computeUnitLimit,
  HELIUS_TIP_MIN_LAMPORTS,
  readConnection,
  shadowEntrySlippagePct,
  shadowExecutionMode,
  shadowExitSlippagePct,
  shadowFailureRatePct,
  shadowLatencyMs,
  shadowRpcSimFailOpen,
  shadowRpcSimulateTx,
  shadowTxFeeSol,
  simulationMode,
  simulationSolBalance,
  solReserveLamports,
  tipLamports,
  useJitoDirect,
  useJitoFallback,
} from "./config.js";
import { getDynamicPriorityFee } from "./feeOracle.js";
import type { MirrorResult } from "./mirror.js";
import { WRAPPED_SOL_MINT } from "./tokenRegistry.js";

let shadowSolLamports =
  Math.round((simulationSolBalance ?? 0) * LAMPORTS_PER_SOL);

function enabled(): boolean {
  return simulationMode && shadowExecutionMode;
}

function clampRaw(raw: number): number {
  return Math.max(0, Math.floor(raw));
}

function deterministicBucket(seed: string): number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) % 10_000;
}

export function estimateShadowFeeLamports(): number {
  const configuredFee = Math.round(shadowTxFeeSol * LAMPORTS_PER_SOL);
  if (configuredFee > 0) return configuredFee;

  const units = Math.min(Math.max(computeUnitLimit || 100_000, 100_000), 1_400_000);
  const priorityLamports = Math.ceil((getDynamicPriorityFee() * units) / 1_000_000);
  const jitoTip = useJitoDirect ? tipLamports : 0;
  const heliusTip = !useJitoDirect || useJitoFallback
    ? useJitoDirect
      ? HELIUS_TIP_MIN_LAMPORTS
      : Math.max(tipLamports, HELIUS_TIP_MIN_LAMPORTS)
    : 0;
  return 5_000 + priorityLamports + jitoTip + heliusTip;
}

export function getShadowSolLamports(): number | null {
  return enabled() ? shadowSolLamports : null;
}

export function shadowCanSpend(spendLamports: number): { ok: boolean; available: number; required: number } {
  const fee = estimateShadowFeeLamports();
  const required = spendLamports + fee;
  const available = Math.max(0, shadowSolLamports - solReserveLamports);
  return { ok: available >= required, available, required };
}

export function shouldShadowFail(seed: string): boolean {
  if (!enabled() || shadowFailureRatePct <= 0) return false;
  return deterministicBucket(seed) < Math.round(shadowFailureRatePct * 100);
}

export async function shadowDelay(): Promise<void> {
  if (!enabled() || shadowLatencyMs <= 0) return;
  await new Promise((resolve) => setTimeout(resolve, shadowLatencyMs));
}

export async function shadowSimulateTransaction(tx: VersionedTransaction, label: string): Promise<void> {
  if (!enabled() || !shadowRpcSimulateTx) return;
  try {
    const result = await readConnection.simulateTransaction(tx, {
      sigVerify: false,
      replaceRecentBlockhash: true,
    });
    if (result.value.err) {
      throw new Error(`${label} RPC simulation failed: ${JSON.stringify(result.value.err)}`);
    }
  } catch (err) {
    if (!shadowRpcSimFailOpen) throw err;
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`Shadow RPC simulation warning (${label}): ${msg}`);
  }
}

export function applyShadowExecution(result: MirrorResult, sourceSignature: string): MirrorResult {
  if (!enabled() || !result.isSimulated) return result;
  if (shouldShadowFail(sourceSignature)) {
    throw new Error(
      `Shadow simulated landing failure (${shadowFailureRatePct.toFixed(1)}% configured failure model)`
    );
  }

  const feeLamports = estimateShadowFeeLamports();
  const adjusted: MirrorResult = {
    ...result,
    input: { ...result.input },
    output: { ...result.output },
  };

  if (adjusted.side === "buy") {
    const penalty = Math.max(0, 1 - shadowEntrySlippagePct / 100);
    adjusted.output.amountRaw = clampRaw(adjusted.output.amountRaw * penalty);
    adjusted.costBasisLamports = (adjusted.costBasisLamports ?? adjusted.input.amountRaw) + feeLamports;
    shadowSolLamports = Math.max(0, shadowSolLamports - adjusted.input.amountRaw - feeLamports);
    console.log(
      `Shadow fill: entry penalty ${shadowEntrySlippagePct.toFixed(1)}%, fee ${(feeLamports / LAMPORTS_PER_SOL).toFixed(6)} SOL, ` +
        `shadow balance ${(shadowSolLamports / LAMPORTS_PER_SOL).toFixed(4)} SOL`
    );
    return adjusted;
  }

  if (adjusted.side === "sell") {
    if (adjusted.output.meta.mint === WRAPPED_SOL_MINT) {
      adjusted.output.amountRaw = applyShadowSellLamports(adjusted.output.amountRaw, sourceSignature);
      return adjusted;
    }
    console.log(
      `Shadow fill: exit penalty ${shadowExitSlippagePct.toFixed(1)}%, fee ${(feeLamports / LAMPORTS_PER_SOL).toFixed(6)} SOL, ` +
        `shadow balance ${(shadowSolLamports / LAMPORTS_PER_SOL).toFixed(4)} SOL`
    );
    return adjusted;
  }

  return adjusted;
}

export function applyShadowSellLamports(outLamports: number, seed: string): number {
  if (!enabled()) return outLamports;
  if (shouldShadowFail(seed)) {
    throw new Error(
      `Shadow simulated landing failure (${shadowFailureRatePct.toFixed(1)}% configured failure model)`
    );
  }
  const feeLamports = estimateShadowFeeLamports();
  const penalty = Math.max(0, 1 - shadowExitSlippagePct / 100);
  const adjustedOut = Math.max(0, clampRaw(outLamports * penalty) - feeLamports);
  shadowSolLamports += adjustedOut;
  console.log(
    `Shadow fill: exit penalty ${shadowExitSlippagePct.toFixed(1)}%, fee ${(feeLamports / LAMPORTS_PER_SOL).toFixed(6)} SOL, ` +
      `shadow balance ${(shadowSolLamports / LAMPORTS_PER_SOL).toFixed(4)} SOL`
  );
  return adjustedOut;
}
