import { Keypair } from "@solana/web3.js";
import { fastMode, simulationMode } from "../config.js";
import { confirmSignature, isConfirmationTimeoutError } from "../confirm.js";
import { recordExecutionStage } from "../executionTelemetry.js";
import { executeRustRaydiumCpmmSwap } from "../rustSidecar.js";
import { getTokenMeta, WRAPPED_SOL_MINT, USDC_MINT, type TokenMeta } from "../tokenRegistry.js";
import type { SourceRaydiumCpmmAccountMeta } from "../types.js";

export const RAYDIUM_CPMM_PROGRAM_ID_BASE58 = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C";
export const RAYDIUM_CPMM_SWAP_BASE_INPUT_DISCRIMINATOR = Buffer.from([143, 190, 90, 218, 196, 30, 51, 222]);

export type RaydiumCpmmDirectResult =
  | {
      ok: true;
      signature: string;
      isSimulated: boolean;
      inputAmount: bigint;
      outputAmount: bigint;
      inputMeta: TokenMeta;
      outputMeta: TokenMeta;
      confirmationPending?: boolean;
    }
  | { ok: false; reason: string; recoverable: boolean };

function minOutFor(expectedOutRaw: number | undefined, slippageBps: number): bigint | null {
  if (!Number.isFinite(expectedOutRaw) || expectedOutRaw == null || expectedOutRaw <= 0) return null;
  const bps = BigInt(Math.max(0, Math.min(10_000, Math.floor(slippageBps))));
  return (BigInt(Math.floor(expectedOutRaw)) * (10_000n - bps)) / 10_000n;
}

function scaledExpectedOut(args: {
  sourceInputRaw: number;
  sourceOutputRaw?: number;
  mirrorInputRaw: number;
}): number | undefined {
  if (
    !Number.isFinite(args.sourceInputRaw) ||
    !Number.isFinite(args.sourceOutputRaw) ||
    !Number.isFinite(args.mirrorInputRaw) ||
    args.sourceInputRaw <= 0 ||
    (args.sourceOutputRaw ?? 0) <= 0 ||
    args.mirrorInputRaw <= 0
  ) {
    return undefined;
  }
  return Math.max(1, Math.floor((args.sourceOutputRaw! * args.mirrorInputRaw) / args.sourceInputRaw));
}

function hasRaydiumAccounts(accounts: SourceRaydiumCpmmAccountMeta[] | string[] | undefined): boolean {
  return Array.isArray(accounts) && accounts.length >= 13;
}

export async function tryRaydiumCpmmDirectBuy(args: {
  user: Keypair;
  mint: string;
  sourceSignature: string;
  sourceInputRaw: number;
  sourceOutputRaw?: number;
  sourceRaydiumCpmmAccounts?: SourceRaydiumCpmmAccountMeta[] | string[];
  quoteMint?: string;
  quoteInRaw: number;
  slippageBps: number;
  startedAt: number;
  log: (message: string) => void;
  onConfirmFailure?: (mint: string, tokensRaw: number, quoteRaw: number, reason: string) => void;
  onConfirmSuccess?: (mint: string, tokensRaw: number, quoteRaw: number, signature: string) => void;
}): Promise<RaydiumCpmmDirectResult> {
  if (!hasRaydiumAccounts(args.sourceRaydiumCpmmAccounts)) {
    return { ok: false, reason: "source Raydium CPMM accounts unavailable", recoverable: true };
  }
  const quoteMint = args.quoteMint ?? WRAPPED_SOL_MINT;
  if (quoteMint !== WRAPPED_SOL_MINT && quoteMint !== USDC_MINT) {
    return { ok: false, reason: `unsupported Raydium quote mint ${quoteMint}`, recoverable: true };
  }
  const expectedOut = scaledExpectedOut({
    sourceInputRaw: args.sourceInputRaw,
    sourceOutputRaw: args.sourceOutputRaw,
    mirrorInputRaw: args.quoteInRaw,
  });
  const minOut = minOutFor(expectedOut, args.slippageBps);
  if (expectedOut == null || minOut == null || minOut <= 0n) {
    return { ok: false, reason: "Raydium source output amount unavailable", recoverable: true };
  }
  const inputMeta = await getTokenMeta(quoteMint);
  const outputMeta = await getTokenMeta(args.mint);
  if (simulationMode) {
    return {
      ok: true,
      signature: `sim-raydium-cpmm-${args.mint.slice(0, 8)}-${Date.now()}`,
      isSimulated: true,
      inputAmount: BigInt(args.quoteInRaw),
      outputAmount: BigInt(expectedOut),
      inputMeta,
      outputMeta,
    };
  }
  const rust = await executeRustRaydiumCpmmSwap({
    route: "raydium-cpmm-swap-base-input",
    side: "buy",
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    inputMint: quoteMint,
    outputMint: args.mint,
    amountInRaw: args.quoteInRaw,
    expectedOutRaw: expectedOut,
    minOutRaw: Number(minOut),
    sourceRaydiumCpmmAccounts: args.sourceRaydiumCpmmAccounts!,
  });
  if (!rust?.ok) {
    return { ok: false, reason: rust?.error ?? "Rust Raydium CPMM executor unavailable", recoverable: true };
  }
  args.log(`Raydium CPMM buy broadcast: ${rust.signature} (${rust.broadcastMs ?? "?"}ms)`);
  recordExecutionStage({
    stage: "confirm",
    route: "raydium-cpmm-swap-base-input",
    side: "buy",
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    signature: rust.signature,
    elapsedMs: Date.now() - args.startedAt,
    success: true,
    details: { mode: fastMode ? "background" : "inline", senderPath: rust.senderPath },
  });
  const confirmationPending = fastMode;
  if (fastMode) {
    confirmSignature(rust.signature)
      .then(() => args.onConfirmSuccess?.(args.mint, rust.tokensOutRaw, args.quoteInRaw, rust.signature))
      .catch((err) => args.onConfirmFailure?.(args.mint, rust.tokensOutRaw, args.quoteInRaw, err instanceof Error ? err.message : String(err)));
  } else {
    try {
      await confirmSignature(rust.signature);
    } catch (err) {
      if (!isConfirmationTimeoutError(err)) throw err;
      args.onConfirmFailure?.(args.mint, rust.tokensOutRaw, args.quoteInRaw, err instanceof Error ? err.message : String(err));
    }
  }
  return {
    ok: true,
    signature: rust.signature,
    isSimulated: false,
    inputAmount: BigInt(args.quoteInRaw),
    outputAmount: BigInt(rust.tokensOutRaw),
    inputMeta,
    outputMeta,
    confirmationPending,
  };
}

export async function tryRaydiumCpmmDirectSell(args: {
  user: Keypair;
  mint: string;
  sourceSignature: string;
  sourceInputRaw: number;
  sourceOutputRaw?: number;
  sourceRaydiumCpmmAccounts?: SourceRaydiumCpmmAccountMeta[] | string[];
  tokensInRaw: number;
  slippageBps: number;
  startedAt: number;
  log: (message: string) => void;
  onConfirmFailure?: (mint: string, tokensRaw: number, quoteRaw: number, reason: string) => void;
  onConfirmSuccess?: (mint: string, tokensRaw: number, quoteRaw: number, signature: string) => void;
}): Promise<RaydiumCpmmDirectResult> {
  if (!hasRaydiumAccounts(args.sourceRaydiumCpmmAccounts)) {
    return { ok: false, reason: "source Raydium CPMM accounts unavailable", recoverable: true };
  }
  const expectedOut = scaledExpectedOut({
    sourceInputRaw: args.sourceInputRaw,
    sourceOutputRaw: args.sourceOutputRaw,
    mirrorInputRaw: args.tokensInRaw,
  });
  const minOut = minOutFor(expectedOut, args.slippageBps);
  if (expectedOut == null || minOut == null || minOut <= 0n) {
    return { ok: false, reason: "Raydium source quote output unavailable", recoverable: true };
  }
  const inputMeta = await getTokenMeta(args.mint);
  const outputMeta = await getTokenMeta(WRAPPED_SOL_MINT);
  if (simulationMode) {
    return {
      ok: true,
      signature: `sim-raydium-cpmm-sell-${args.mint.slice(0, 8)}-${Date.now()}`,
      isSimulated: true,
      inputAmount: BigInt(args.tokensInRaw),
      outputAmount: BigInt(expectedOut),
      inputMeta,
      outputMeta,
    };
  }
  const rust = await executeRustRaydiumCpmmSwap({
    route: "raydium-cpmm-swap-base-input",
    side: "sell",
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    inputMint: args.mint,
    outputMint: WRAPPED_SOL_MINT,
    amountInRaw: args.tokensInRaw,
    expectedOutRaw: expectedOut,
    minOutRaw: Number(minOut),
    sourceRaydiumCpmmAccounts: args.sourceRaydiumCpmmAccounts!,
  });
  if (!rust?.ok) {
    return { ok: false, reason: rust?.error ?? "Rust Raydium CPMM executor unavailable", recoverable: true };
  }
  args.log(`Raydium CPMM sell broadcast: ${rust.signature} (${rust.broadcastMs ?? "?"}ms)`);
  const confirmationPending = fastMode;
  if (fastMode) {
    confirmSignature(rust.signature)
      .then(() => args.onConfirmSuccess?.(args.mint, args.tokensInRaw, rust.tokensOutRaw, rust.signature))
      .catch((err) => args.onConfirmFailure?.(args.mint, args.tokensInRaw, rust.tokensOutRaw, err instanceof Error ? err.message : String(err)));
  } else {
    await confirmSignature(rust.signature);
  }
  return {
    ok: true,
    signature: rust.signature,
    isSimulated: false,
    inputAmount: BigInt(args.tokensInRaw),
    outputAmount: BigInt(rust.tokensOutRaw),
    inputMeta,
    outputMeta,
    confirmationPending,
  };
}

