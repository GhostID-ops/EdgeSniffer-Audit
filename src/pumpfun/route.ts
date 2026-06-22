import { Keypair, PublicKey, type AccountMeta } from "@solana/web3.js";
import { broadcastTransactionWithSender } from "../sender.js";
import { confirmSignature, isConfirmationTimeoutError } from "../confirm.js";
import {
  confirmationRebroadcastRetries,
  fastMode,
  localPumpfunMarketCapFilterEnabled,
  localPumpfunMaxMarketCapSol,
  localPumpfunMinMarketCapSol,
  localPumpfunSolPriceUsd,
  simulationMode,
  mirrorRetrySlippageBps,
  pumpfunExactSolInBuys,
  prebuyFilterFailOpen,
  prebuyMaxMarketCapUsd,
  prebuyMinMarketCapUsd,
  readConnection,
  rustOnlyBuys,
  rustOnlySells,
  usePumpfunToken2022DirectSell,
} from "../config.js";
import { getTokenMeta, type TokenMeta, USDC_MINT, WRAPPED_SOL_MINT } from "../tokenRegistry.js";
import {
  PUMP_FUN_PROGRAM_ID,
  PUMP_FUN_BUYBACK_FEE_RECIPIENT,
  TOKEN_2022_PROGRAM_ID,
} from "./constants.js";
import {
  getBondingCurveState,
  estimateMarketCapSolOnCurve,
  quoteBuyOnCurve,
  quoteSellOnCurve,
  maxSolForImpact,
} from "./curve.js";
import { buildPumpFunBuyReplayTransaction, buildPumpFunBuyTransaction, scalePumpFunSourceBuyData } from "./buy.js";
import {
  buildPumpFunSellIx,
  buildPumpFunSellTransaction,
  buildPumpFunSellV2Ix,
} from "./sell.js";
import { recordExecutionStage } from "../executionTelemetry.js";
import { executeRustPumpFunBuy, executeRustPumpFunSell } from "../rustSidecar.js";

const DIRECT_CURVE_RETRY_DELAYS_MS = [25, 50, 100];

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function recordConfirmStage(args: {
  route: string;
  side: "buy" | "sell";
  mint: string;
  sourceSignature?: string;
  signature?: string;
  startedAt: number;
  success: boolean;
  error?: string;
  details?: Record<string, unknown>;
}): void {
  recordExecutionStage({
    stage: "confirm",
    route: args.route,
    side: args.side,
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    signature: args.signature,
    elapsedMs: Date.now() - args.startedAt,
    success: args.success,
    error: args.error,
    details: args.details,
  });
}

function formatMcSol(value: number): string {
  return `${value.toFixed(value >= 100 ? 1 : 2)} SOL`;
}

function formatMcUsd(value: number): string {
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  return `$${value.toFixed(0)}`;
}

function localPumpfunMarketCapGate(state: { marketCapSol: number | null }): {
  allowed: boolean;
  reason?: string;
} {
  if (!localPumpfunMarketCapFilterEnabled) return { allowed: true };
  if (state.marketCapSol == null) {
    return {
      allowed: prebuyFilterFailOpen,
      reason: `local Pump.fun MC unavailable (${prebuyFilterFailOpen ? "fail-open" : "fail-closed"})`,
    };
  }

  const minSolFromUsd =
    prebuyMinMarketCapUsd != null && localPumpfunSolPriceUsd != null
      ? prebuyMinMarketCapUsd / localPumpfunSolPriceUsd
      : null;
  const maxSolFromUsd =
    prebuyMaxMarketCapUsd != null && localPumpfunSolPriceUsd != null
      ? prebuyMaxMarketCapUsd / localPumpfunSolPriceUsd
      : null;
  if (
    (prebuyMinMarketCapUsd != null || prebuyMaxMarketCapUsd != null) &&
    localPumpfunSolPriceUsd == null &&
    localPumpfunMinMarketCapSol == null &&
    localPumpfunMaxMarketCapSol == null
  ) {
    return {
      allowed: prebuyFilterFailOpen,
      reason:
        `local Pump.fun MC needs LOCAL_PUMPFUN_SOL_PRICE_USD for USD thresholds ` +
        `(${prebuyFilterFailOpen ? "fail-open" : "fail-closed"})`,
    };
  }

  const minSol =
    localPumpfunMinMarketCapSol != null && minSolFromUsd != null
      ? Math.max(localPumpfunMinMarketCapSol, minSolFromUsd)
      : localPumpfunMinMarketCapSol ?? minSolFromUsd;
  const maxSol =
    localPumpfunMaxMarketCapSol != null && maxSolFromUsd != null
      ? Math.min(localPumpfunMaxMarketCapSol, maxSolFromUsd)
      : localPumpfunMaxMarketCapSol ?? maxSolFromUsd;

  const usd = localPumpfunSolPriceUsd != null ? state.marketCapSol * localPumpfunSolPriceUsd : null;
  const label = usd != null
    ? `${formatMcUsd(usd)} (${formatMcSol(state.marketCapSol)})`
    : formatMcSol(state.marketCapSol);
  if (minSol != null && state.marketCapSol < minSol) {
    const minLabel =
      prebuyMinMarketCapUsd != null && minSol === minSolFromUsd
        ? formatMcUsd(prebuyMinMarketCapUsd)
        : formatMcSol(minSol);
    return { allowed: false, reason: `local Pump.fun MC ${label} < ${minLabel}` };
  }
  if (maxSol != null && state.marketCapSol > maxSol) {
    const maxLabel =
      prebuyMaxMarketCapUsd != null && maxSol === maxSolFromUsd
        ? formatMcUsd(prebuyMaxMarketCapUsd)
        : formatMcSol(maxSol);
    return { allowed: false, reason: `local Pump.fun MC ${label} > ${maxLabel}` };
  }
  return { allowed: true, reason: `local Pump.fun MC passed: ${label}` };
}

export type PumpFunBuyOutcome =
  | {
      ok: true;
      signature: string;
      isSimulated: boolean;
      tokensOut: bigint;
      solIn: bigint;
      priceImpactPct: number;
      mintMeta: TokenMeta;
      confirmationPending?: boolean;
    }
  | { ok: false; reason: string; recoverable: boolean };

function isPumpFunRetryableBuyFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return msg.includes("\"Custom\":6002") || msg.includes("Custom:6002");
}

function isPumpFunSellUnsupportedTokenProgram(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return (
    msg.includes("\"Custom\":3008") ||
    msg.includes("\"Custom\":6057") ||
    msg.includes("Custom:3008") ||
    msg.includes("Custom:6057") ||
    msg.includes("BuybackFeeRecipientNotAuthorized") ||
    msg.includes("InvalidProgramId")
  );
}

async function getSourcePumpRemainingAccounts(
  sourceSignature: string | undefined,
  mint: PublicKey
): Promise<AccountMeta[]> {
  if (!sourceSignature || sourceSignature.startsWith("manual-")) return [];
  try {
    // NOTE: getTransaction only supports confirmed/finalized — "processed" isn't allowed by
    // the RPC spec. On hot launches gRPC arrives 1-2s ahead of confirmation, so this fetch
    // routinely returns null. The hardcoded fallback in tryPumpFunDirectBuy covers that gap.
    const tx = await readConnection.getTransaction(sourceSignature, {
      commitment: "confirmed",
      maxSupportedTransactionVersion: 0,
    });
    if (!tx) return [];
    const message = tx.transaction.message;
    const staticKeys = message.staticAccountKeys;
    const loaded = tx.meta?.loadedAddresses
      ? [...tx.meta.loadedAddresses.writable, ...tx.meta.loadedAddresses.readonly]
      : [];
    const allKeys = [...staticKeys, ...loaded];

    // Normalize top-level + inner ixs into one shape. Routers (e.g. Photon, BananaGun,
    // BullX, term9YPb…) wrap pump.fun in a CPI, so the buy is an inner instruction.
    // The previous version only walked top-level ixs and silently returned [] for
    // router-routed trades, causing 6062 BuybackFeeRecipientMissing on Token-2022.
    type IxView = { programIdIndex: number; accountIndexes: number[] };
    const candidates: IxView[] = [];
    for (const ix of message.compiledInstructions) {
      candidates.push({
        programIdIndex: ix.programIdIndex,
        accountIndexes: [...ix.accountKeyIndexes],
      });
    }
    for (const group of tx.meta?.innerInstructions ?? []) {
      for (const ix of group.instructions) {
        const innerIdxs =
          (ix as { accounts?: number[] }).accounts ??
          (ix as { accountKeyIndexes?: number[] }).accountKeyIndexes ??
          [];
        candidates.push({
          programIdIndex: ix.programIdIndex,
          accountIndexes: innerIdxs,
        });
      }
    }

    for (const ix of candidates) {
      const programId = allKeys[ix.programIdIndex];
      if (!programId?.equals(PUMP_FUN_PROGRAM_ID)) continue;
      const accountKeys = ix.accountIndexes.map((idx) => allKeys[idx]);
      if (accountKeys.length < 18) continue;
      if (!accountKeys[2]?.equals(mint)) continue;
      if (!accountKeys[11]?.equals(PUMP_FUN_PROGRAM_ID)) continue;

      const remaining = ix.accountIndexes.slice(16).map((idx) => ({
        pubkey: allKeys[idx]!,
        isSigner: message.isAccountSigner(idx),
        isWritable: message.isAccountWritable(idx),
      }));
      if (remaining.length > 0) {
        return remaining;
      }
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.warn(`Could not inspect source pump.fun accounts: ${msg}`);
  }
  return [];
}

/**
 * Attempt a direct pump.fun bonding-curve buy. Returns:
 *   { ok: true, ... }                    — buy succeeded
 *   { ok: false, recoverable: true, ... } — caller should fall through to Jupiter (e.g. token not on curve, complete=true)
 *   { ok: false, recoverable: false, ... }— hard failure, don't retry on Jupiter (e.g. broadcast error)
 *
 * `maxImpactPct` is optional. If set and the requested size exceeds the cap, this function will
 * shrink the size in-line using the curve math (no extra RPC) — much faster and more accurate
 * than the Jupiter probe-and-shrink loop.
 */
export async function tryPumpFunDirectBuy(args: {
  user: Keypair;
  mint: string;
  quoteMint?: string;
  solInLamports: number;
  slippageBps: number;
  maxImpactPct?: number | null;
  minSolLamports: number;
  startedAt: number;
  log: (msg: string) => void;
  sourceSignature?: string;
  /**
   * Full account list from the source's pump.fun buy ix (16-18 base58 pubkeys), extracted
   * by the gRPC watcher from the live tx payload. When present, the buy builder uses these
   * exact slots (with user-specific slots 5/6/13 swapped) instead of deriving — required
   * for Token-2022 launches where slot 1 and slots 16+ rotate per-coin and aren't derivable.
   */
  sourceIxAccounts?: string[];
  sourceIxDataBase64?: string;
  onConfirmFailure?: (mint: string, tokensOutRaw: number, solInLamports: number, reason: string) => void;
  onConfirmSuccess?: (mint: string, tokensOutRaw: number, solInLamports: number, signature: string) => void;
  rebroadcastAttempt?: number;
}): Promise<PumpFunBuyOutcome> {
  const elapsed = () => `${Date.now() - args.startedAt}ms`;
  const mintKey = new PublicKey(args.mint);

  // 1. Detect curve. Fire the getTransaction fallback for source accounts in parallel ONLY
  // when gRPC didn't already provide them — otherwise it's a wasted RPC that competes with
  // the curve fetch for Shyft bandwidth and adds nothing.
  const haveGrpcAccounts =
    args.sourceIxAccounts !== undefined && args.sourceIxAccounts.length >= 16;
  const sourceAccountsPromise: Promise<AccountMeta[]> = haveGrpcAccounts
    ? Promise.resolve([])
    : getSourcePumpRemainingAccounts(args.sourceSignature, mintKey);
  const quoteMint = args.quoteMint ?? WRAPPED_SOL_MINT;
  const isUsdcQuote = quoteMint === USDC_MINT;
  let curve = isUsdcQuote ? null : await getBondingCurveState(mintKey);
  const canSourceReplay =
    args.sourceIxAccounts !== undefined &&
    args.sourceIxAccounts.length >= 16 &&
    Boolean(args.sourceIxDataBase64);

  if (!curve && canSourceReplay && !isUsdcQuote) {
    for (const delayMs of DIRECT_CURVE_RETRY_DELAYS_MS) {
      await sleep(delayMs);
      curve = await getBondingCurveState(mintKey);
      if (curve) {
        args.log(
          `Pump.fun curve RPC became available after ${delayMs}ms retry; using direct curve quote instead of source replay.`
        );
        break;
      }
    }
  }
  if (!curve) {
    if (localPumpfunMarketCapFilterEnabled && !isUsdcQuote) {
      const mcGate = localPumpfunMarketCapGate({ marketCapSol: null });
      if (mcGate.reason) args.log(`${mcGate.reason}; curve unavailable before source replay.`);
      if (!mcGate.allowed) {
        return {
          ok: false,
          recoverable: false,
          reason: mcGate.reason ?? "local Pump.fun market-cap filter blocked source replay",
        };
      }
    }
    if (canSourceReplay) {
      args.log("Pump.fun curve RPC unavailable after direct retries; replaying scaled source Pump.fun ix.");
      const sourceIxAccounts = args.sourceIxAccounts!;
      const sourceIxDataBase64 = args.sourceIxDataBase64!;
      const sourceIxPubkeys = sourceIxAccounts.map((k) => new PublicKey(k));
      const sourceIxData = Buffer.from(sourceIxDataBase64, "base64");
      const solIn = BigInt(args.solInLamports);
      const route = "pumpfun-source-replay-buy";
      if (simulationMode) {
        const mintMeta = await getTokenMeta(args.mint);
        const scaled = scalePumpFunSourceBuyData({
          sourceIxData,
          solInLamports: solIn,
          slippageBps: args.slippageBps,
        });
        return {
          ok: true,
          signature: `sim-pumpfun-replay-${args.mint.slice(0, 8)}-${Date.now()}`,
          isSimulated: true,
          tokensOut: scaled.estimatedTokensOut,
          solIn,
          priceImpactPct: 0,
          mintMeta,
        };
      }
      args.log("Building pump.fun source-replay buy tx...");
      let signature: string;
      let estimatedTokensOut: bigint;
      const buildStartedAt = Date.now();
      const rust = await executeRustPumpFunBuy({
        mode: "source-replay",
        route,
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        quoteMint,
        solInLamports: Number(solIn),
        slippageBps: args.slippageBps,
        exactSolIn: false,
        sourceIxAccounts,
        sourceIxDataBase64,
      });
      if (rust?.ok) {
        signature = rust.signature;
        estimatedTokensOut = BigInt(rust.tokensOutRaw);
        recordExecutionStage({
          stage: "build",
          route,
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          elapsedMs: rust.buildMs ?? Date.now() - buildStartedAt,
          success: true,
          details: {
            executor: "rust",
            senderPath: rust.senderPath,
            txBytes: rust.txBytes,
            broadcastMs: rust.broadcastMs,
          },
        });
        recordExecutionStage({
          stage: "broadcast",
          route,
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          signature,
          elapsedMs: rust.broadcastMs,
          success: true,
          details: { executor: "rust", senderPath: rust.senderPath },
        });
        args.log(
          `Rust executor source-replay broadcast: ${signature}` +
            (rust.elapsedMs != null ? ` (${rust.elapsedMs}ms)` : "")
        );
      } else {
        if (rustOnlyBuys) {
          const reason = rust && !rust.ok ? rust.error : "Rust executor unavailable";
          args.log(`Rust-only source-replay buy failed; TypeScript fallback disabled: ${reason}`);
          return { ok: false, recoverable: true, reason: `Rust-only source-replay buy failed: ${reason}` };
        }
        if (rust && !rust.ok) args.log(`Rust executor source-replay failed; falling back to TypeScript: ${rust.error}`);
      try {
        const replay = await buildPumpFunBuyReplayTransaction({
          user: args.user,
          mint: mintKey,
          solInLamports: solIn,
          slippageBps: args.slippageBps,
          sourceIxAccounts: sourceIxPubkeys,
          sourceIxData,
        });
        estimatedTokensOut = replay.estimatedTokensOut;
        recordExecutionStage({
          stage: "build",
          route: "pumpfun-source-replay-buy",
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          elapsedMs: Date.now() - buildStartedAt,
          success: true,
        });
        args.log(`Tx built (${elapsed()})`);
        signature = await broadcastTransactionWithSender(replay.tx, {
          preferHelius: (args.rebroadcastAttempt ?? 0) > 0,
          route: "pumpfun-source-replay-buy",
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
        });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        recordExecutionStage({
          stage: "build",
          route: "pumpfun-source-replay-buy",
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          elapsedMs: Date.now() - buildStartedAt,
          success: false,
          error: msg,
        });
        return { ok: false, recoverable: false, reason: `source-replay failed: ${msg}` };
      }
      }
      args.log(`Broadcast: ${signature} (${elapsed()})`);
      if (fastMode) {
        const confirmStartedAt = Date.now();
        confirmSignature(signature)
          .then(() => {
            recordConfirmStage({
              route: "pumpfun-source-replay-buy",
              side: "buy",
              mint: args.mint,
              sourceSignature: args.sourceSignature,
              signature,
              startedAt: confirmStartedAt,
              success: true,
              details: { mode: "background" },
            });
            args.onConfirmSuccess?.(args.mint, Number(estimatedTokensOut), Number(solIn), signature);
          })
          .catch((err) => {
            const reason = err instanceof Error ? err.message : String(err);
            recordConfirmStage({
              route: "pumpfun-source-replay-buy",
              side: "buy",
              mint: args.mint,
              sourceSignature: args.sourceSignature,
              signature,
              startedAt: confirmStartedAt,
              success: false,
              error: reason,
              details: { mode: "background" },
            });
            args.onConfirmFailure?.(args.mint, Number(estimatedTokensOut), Number(solIn), reason);
          });
        const mintMeta = await getTokenMeta(args.mint);
        return {
          ok: true,
          signature,
          isSimulated: false,
          tokensOut: estimatedTokensOut,
          solIn,
          priceImpactPct: 0,
          mintMeta,
          confirmationPending: true,
        };
      }
      const confirmStartedAt = Date.now();
      try {
        await confirmSignature(signature);
        recordConfirmStage({
          route: "pumpfun-source-replay-buy",
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          signature,
          startedAt: confirmStartedAt,
          success: true,
          details: { mode: "blocking" },
        });
      } catch (err) {
        const reason = err instanceof Error ? err.message : String(err);
        recordConfirmStage({
          route: "pumpfun-source-replay-buy",
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          signature,
          startedAt: confirmStartedAt,
          success: false,
          error: reason,
          details: { mode: "blocking" },
        });
        throw err;
      }
      const mintMeta = await getTokenMeta(args.mint);
      return {
        ok: true,
        signature,
        isSimulated: false,
        tokensOut: estimatedTokensOut,
        solIn,
        priceImpactPct: 0,
        mintMeta,
      };
    }
    return { ok: false, recoverable: true, reason: "not a pump.fun bonding-curve token" };
  }
  if (curve.state.complete) {
    return { ok: false, recoverable: true, reason: "pump.fun token has graduated" };
  }
  if (!curve.state.creator) {
    return { ok: false, recoverable: true, reason: "pump.fun bonding curve missing creator field" };
  }
  args.log(
    `Pump.fun curve detected (${elapsed()}); virtSol=${(Number(curve.state.virtualSolReserves) / 1e9).toFixed(2)} SOL · tokenProg=${curve.tokenProgramId.toBase58().slice(0, 8)}…`
  );
  const localMarketCapSol = estimateMarketCapSolOnCurve(curve.state);
  const mcGate = localPumpfunMarketCapGate({ marketCapSol: localMarketCapSol });
  if (localPumpfunMarketCapFilterEnabled && mcGate.reason) {
    args.log(mcGate.reason);
  }
  if (!mcGate.allowed) {
    return { ok: false, recoverable: false, reason: mcGate.reason ?? "local Pump.fun market-cap filter blocked" };
  }

  // 2. Liquidity-aware sizing: if size exceeds the impact cap, shrink to fit using exact curve math.
  let solIn = BigInt(args.solInLamports);
  if (args.maxImpactPct != null && args.maxImpactPct > 0) {
    const maxFit = maxSolForImpact(curve.state, args.maxImpactPct);
    if (maxFit < solIn) {
      if (maxFit < BigInt(args.minSolLamports)) {
        return {
          ok: false,
          recoverable: false,
          reason: `pump.fun curve too thin: max ${(Number(maxFit) / 1e9).toFixed(4)} SOL fits cap ${args.maxImpactPct}% but MIN_BUY is ${(args.minSolLamports / 1e9).toFixed(4)} SOL`,
        };
      }
      args.log(
        `Curve-aware sizing: ${(Number(solIn) / 1e9).toFixed(4)} → ${(Number(maxFit) / 1e9).toFixed(4)} SOL to fit ${args.maxImpactPct}% impact`
      );
      solIn = maxFit;
    }
  }

  // 3. Quote: compute exact tokens_out for solIn.
  const quote = quoteBuyOnCurve(curve.state, solIn);
  args.log(
    `Curve quote: in=${(Number(quote.solIn) / 1e9).toFixed(4)} SOL fee=${(Number(quote.feeLamports) / 1e9).toFixed(6)} SOL out=${quote.tokensOut} impact=${quote.priceImpactPct.toFixed(2)}%`
  );

  // 4. Simulation mode: skip broadcast, return synthetic result.
  if (simulationMode) {
    const mintMeta = await getTokenMeta(args.mint);
    return {
      ok: true,
      signature: `sim-pumpfun-${args.mint.slice(0, 8)}-${Date.now()}`,
      isSimulated: true,
      tokensOut: quote.tokensOut,
      solIn: quote.solIn,
      priceImpactPct: quote.priceImpactPct,
      mintMeta,
    };
  }

  // 5. Build + sign + broadcast.
  args.log("Building pump.fun buy tx...");
  const buildStartedAt = Date.now();
  // Prefer canonical curve-derived V2 accounts when the curve RPC is available. Source CPI metas
  // can carry router-specific or stale accounts and have repeatedly failed with Pump.fun 2006/6004.
  // Source metas remain useful for legacy per-coin remaining accounts, and for the earlier
  // source-replay fallback when the curve account is not readable yet.
  let sourceIxPubkeys: PublicKey[] | undefined;
  if (args.sourceIxAccounts && args.sourceIxAccounts.length >= 16) {
    const candidate = args.sourceIxAccounts.map((k) => new PublicKey(k));
    const isV2Source =
      candidate.length >= 27 &&
      candidate[1]?.equals(mintKey) === true &&
      candidate[26]?.equals(PUMP_FUN_PROGRAM_ID) === true;
    if (curve.tokenProgramId.equals(TOKEN_2022_PROGRAM_ID)) {
      args.log(
        isV2Source
          ? "Using canonical Pump.fun V2 account derivation; ignoring source V2 metas after curve RPC quote."
          : "Using canonical Pump.fun V2 account derivation; ignoring legacy source metas for Token-2022 direct buy."
      );
    } else {
      sourceIxPubkeys = candidate;
      args.log(`Using ${sourceIxPubkeys.length} source pump.fun account(s) from gRPC payload.`);
    }
  }
  const remainingAccounts = sourceIxPubkeys ? [] : await sourceAccountsPromise;
  if (!sourceIxPubkeys && remainingAccounts.length > 0) {
    args.log(`Using ${remainingAccounts.length} source pump.fun remaining account(s) from getTransaction.`);
  }
  let tx;
  let signature: string | undefined;
  const rust = await executeRustPumpFunBuy({
    mode: "direct",
    route: "pumpfun-direct-buy",
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    solInLamports: Number(quote.solIn),
    tokensOutRaw: Number(quote.tokensOut),
    slippageBps: args.slippageBps,
    exactSolIn: pumpfunExactSolInBuys,
    bondingCurve: curve.pda.toBase58(),
    creator: curve.state.creator.toBase58(),
    tokenProgramId: curve.tokenProgramId.toBase58(),
    sourceIxAccounts: sourceIxPubkeys?.map((k) => k.toBase58()),
  });
  if (rust?.ok) {
    signature = rust.signature;
    recordExecutionStage({
      stage: "build",
      route: "pumpfun-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: rust.buildMs ?? Date.now() - buildStartedAt,
      success: true,
      details: {
        executor: "rust",
        senderPath: rust.senderPath,
        txBytes: rust.txBytes,
        broadcastMs: rust.broadcastMs,
      },
    });
    recordExecutionStage({
      stage: "broadcast",
      route: "pumpfun-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      signature,
      elapsedMs: rust.broadcastMs,
      success: true,
      details: { executor: "rust", senderPath: rust.senderPath },
    });
    args.log(
      `Rust executor direct-buy broadcast: ${signature}` +
        (rust.elapsedMs != null ? ` (${rust.elapsedMs}ms)` : "")
    );
  } else {
    if (rustOnlyBuys) {
      const reason = rust && !rust.ok ? rust.error : "Rust executor unavailable";
      args.log(`Rust-only direct buy failed; TypeScript fallback disabled: ${reason}`);
      return { ok: false, recoverable: true, reason: `Rust-only direct buy failed: ${reason}` };
    }
    if (rust && !rust.ok) args.log(`Rust executor direct-buy failed; falling back to TypeScript: ${rust.error}`);
  try {
    tx = await buildPumpFunBuyTransaction({
    user: args.user,
    mint: mintKey,
    bondingCurve: curve.pda,
    creator: curve.state.creator,
    tokenProgramId: curve.tokenProgramId,
    tokensOut: quote.tokensOut,
    solInLamports: quote.solIn,
    slippageBps: args.slippageBps,
    remainingAccounts,
    sourceIxAccounts: sourceIxPubkeys,
    });
    recordExecutionStage({
      stage: "build",
      route: "pumpfun-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: Date.now() - buildStartedAt,
      success: true,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    recordExecutionStage({
      stage: "build",
      route: "pumpfun-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: Date.now() - buildStartedAt,
      success: false,
      error: msg,
    });
    return { ok: false, recoverable: true, reason: msg };
  }
  args.log(`Tx built (${elapsed()})`);

  try {
    signature = await broadcastTransactionWithSender(tx, {
      preferHelius: (args.rebroadcastAttempt ?? 0) > 0,
      route: "pumpfun-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, recoverable: false, reason: `broadcast failed: ${msg}` };
  }
  }
  if (!signature) {
    return { ok: false, recoverable: false, reason: "broadcast failed: no signature returned" };
  }
  args.log(`Broadcast: ${signature} (${elapsed()})`);

  if (fastMode) {
    // Fast mode records after broadcast so snipes are not delayed by confirmation polling.
    const confirmStartedAt = Date.now();
    confirmSignature(signature)
      .then(() => {
        recordConfirmStage({
          route: "pumpfun-direct-buy",
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          signature,
          startedAt: confirmStartedAt,
          success: true,
          details: { mode: "background" },
        });
        args.onConfirmSuccess?.(args.mint, Number(quote.tokensOut), Number(quote.solIn), signature);
      })
      .catch((err) => {
        const reason = err instanceof Error ? err.message : String(err);
        recordConfirmStage({
          route: "pumpfun-direct-buy",
          side: "buy",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          signature,
          startedAt: confirmStartedAt,
          success: false,
          error: reason,
          details: { mode: "background" },
        });
        args.onConfirmFailure?.(args.mint, Number(quote.tokensOut), Number(quote.solIn), reason);
      });
    const mintMeta = await getTokenMeta(args.mint);
    return {
      ok: true,
      signature,
      isSimulated: false,
      tokensOut: quote.tokensOut,
      solIn: quote.solIn,
      priceImpactPct: quote.priceImpactPct,
      mintMeta,
      confirmationPending: true,
    };
  }
  const confirmStartedAt = Date.now();
  try {
    await confirmSignature(signature);
    recordConfirmStage({
      route: "pumpfun-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      signature,
      startedAt: confirmStartedAt,
      success: true,
      details: { mode: "blocking" },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    recordConfirmStage({
      route: "pumpfun-direct-buy",
      side: "buy",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      signature,
      startedAt: confirmStartedAt,
      success: false,
      error: msg,
      details: { mode: "blocking", rebroadcastAttempt: args.rebroadcastAttempt ?? 0 },
    });
    const rebroadcastAttempt = args.rebroadcastAttempt ?? 0;
    if (isConfirmationTimeoutError(err) && rebroadcastAttempt < confirmationRebroadcastRetries) {
      // A never-seen signature usually means the bundle was dropped; rebuild with fresh curve state/blockhash.
      args.log(`Pump.fun buy suspected dropped (${msg}); rebuilding once with fresh blockhash...`);
      return tryPumpFunDirectBuy({
        ...args,
        rebroadcastAttempt: rebroadcastAttempt + 1,
      });
    }
    if (isPumpFunRetryableBuyFailure(err) && mirrorRetrySlippageBps > args.slippageBps) {
      args.log(
        `Pump.fun buy exceeded slippage; retrying fresh quote at ${(mirrorRetrySlippageBps / 100).toFixed(2)}% slippage...`
      );
      return tryPumpFunDirectBuy({
        ...args,
        slippageBps: mirrorRetrySlippageBps,
      });
    }
    throw err;
  }
  args.log(`Confirmed (${elapsed()})`);

  const mintMeta = await getTokenMeta(args.mint);
  return {
    ok: true,
    signature,
    isSimulated: false,
    tokensOut: quote.tokensOut,
    solIn: quote.solIn,
    priceImpactPct: quote.priceImpactPct,
    mintMeta,
  };
}

/**
 * Convenience: synthesize a TokenMeta for SOL so callers can build MirrorResult shape uniformly.
 * Avoids an unrelated import dance in mirror.ts.
 */
export async function getSolMeta(): Promise<TokenMeta> {
  return getTokenMeta(WRAPPED_SOL_MINT);
}

export type PumpFunSellOutcome =
  | {
      ok: true;
      signature: string;
      isSimulated: boolean;
      tokensIn: bigint;
      solOut: bigint;
      priceImpactPct: number;
      mintMeta: TokenMeta;
      confirmationPending?: boolean;
    }
  | { ok: false; reason: string; recoverable: boolean };

/**
 * Attempt a direct pump.fun bonding-curve sell. Symmetric with tryPumpFunDirectBuy.
 *   ok: true                           — sell succeeded
 *   ok: false, recoverable: true       — caller should fall through to Jupiter (graduated, not on curve)
 *   ok: false, recoverable: false      — hard failure, don't retry on Jupiter
 */
export async function tryPumpFunDirectSell(args: {
  user: Keypair;
  mint: string;
  tokensInRaw: bigint;
  slippageBps: number;
  startedAt: number;
  log: (msg: string) => void;
  sourceSignature?: string;
  onConfirmFailure?: (mint: string, tokensInRaw: number, solOutLamports: number, reason: string) => void;
  onConfirmSuccess?: (mint: string, tokensInRaw: number, solOutLamports: number, signature: string) => void;
  rebroadcastAttempt?: number;
}): Promise<PumpFunSellOutcome> {
  const elapsed = () => `${Date.now() - args.startedAt}ms`;
  const mintKey = new PublicKey(args.mint);

  const curve = await getBondingCurveState(mintKey);
  if (!curve) {
    return { ok: false, recoverable: true, reason: "not a pump.fun bonding-curve token" };
  }
  if (curve.state.complete) {
    return { ok: false, recoverable: true, reason: "pump.fun token has graduated" };
  }
  if (!curve.state.creator) {
    return { ok: false, recoverable: true, reason: "pump.fun bonding curve missing creator field" };
  }
  args.log(
    `Pump.fun curve detected (${elapsed()}); virtSol=${(Number(curve.state.virtualSolReserves) / 1e9).toFixed(2)} SOL`
  );

  const quote = quoteSellOnCurve(curve.state, args.tokensInRaw);
  args.log(
    `Curve sell quote: in=${args.tokensInRaw} fee=${(Number(quote.feeLamports) / 1e9).toFixed(6)} SOL out=${(Number(quote.solOut) / 1e9).toFixed(4)} SOL impact=${quote.priceImpactPct.toFixed(2)}%`
  );

  if (simulationMode) {
    const mintMeta = await getTokenMeta(args.mint);
    return {
      ok: true,
      signature: `sim-pumpfun-sell-${args.mint.slice(0, 8)}-${Date.now()}`,
      isSimulated: true,
      tokensIn: quote.tokensIn,
      solOut: quote.solOut,
      priceImpactPct: quote.priceImpactPct,
      mintMeta,
    };
  }

  args.log("Building pump.fun sell tx...");
  const buildStartedAt = Date.now();
  const isToken2022 = curve.tokenProgramId.equals(TOKEN_2022_PROGRAM_ID);
  if (isToken2022 && !usePumpfunToken2022DirectSell) {
    return {
      ok: false,
      recoverable: true,
      reason: "Token-2022 direct sell disabled; falling back to Jupiter",
    };
  }
  const useSellV2 = isToken2022;
  const remainingAccounts = useSellV2
    ? []
    : await getSourcePumpRemainingAccounts(args.sourceSignature, mintKey);
  if (useSellV2) {
    args.log("Using pump.fun sell_v2 for Token-2022 mint.");
  } else if (remainingAccounts.length > 0) {
    args.log(`Using ${remainingAccounts.length} source pump.fun remaining account(s) for fee/buyback routing.`);
  }
  const minSolOutput =
    (quote.solOut * BigInt(10_000 - Math.max(0, Math.min(10_000, args.slippageBps)))) / 10_000n;
  const rustSellIx = useSellV2
    ? buildPumpFunSellV2Ix({
        user: args.user.publicKey,
        mint: mintKey,
        bondingCurve: curve.pda,
        creator: curve.state.creator,
        baseTokenProgramId: curve.tokenProgramId,
        amount: args.tokensInRaw,
        minSolOutput,
        buybackFeeRecipient: PUMP_FUN_BUYBACK_FEE_RECIPIENT,
      })
    : buildPumpFunSellIx({
        user: args.user.publicKey,
        mint: mintKey,
        bondingCurve: curve.pda,
        creator: curve.state.creator,
        tokenProgramId: curve.tokenProgramId,
        amount: args.tokensInRaw,
        minSolOutput,
        remainingAccounts,
      });
  const rust = await executeRustPumpFunSell({
    route: useSellV2 ? "pumpfun-direct-sell-v2" : "pumpfun-direct-sell",
    mint: args.mint,
    tokensInRaw: Number(quote.tokensIn),
    expectedSolOutLamports: Number(quote.solOut),
    minSolOutLamports: Number(minSolOutput),
    sellV2: useSellV2,
    accounts: rustSellIx.keys.map((account) => ({
      pubkey: account.pubkey.toBase58(),
      isSigner: account.isSigner,
      isWritable: account.isWritable,
    })),
  });
  if (rust?.ok) {
    const signature = rust.signature;
    args.log(
      `Rust executor Pump.fun sell broadcast: ${signature}` +
        (rust.senderPath ? ` via ${rust.senderPath}` : "") +
        (rust.elapsedMs != null ? ` (${rust.elapsedMs}ms)` : "")
    );
    recordExecutionStage({
      stage: "build",
      route: useSellV2 ? "pumpfun-direct-sell-v2" : "pumpfun-direct-sell",
      side: "sell",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: rust.buildMs ?? Date.now() - buildStartedAt,
      success: true,
      details: { executor: "rust", txBytes: rust.txBytes, senderPath: rust.senderPath },
    });
    if (fastMode) {
      const confirmStartedAt = Date.now();
      confirmSignature(signature)
        .then(() => {
          recordConfirmStage({
            route: useSellV2 ? "pumpfun-direct-sell-v2" : "pumpfun-direct-sell",
            side: "sell",
            mint: args.mint,
            sourceSignature: args.sourceSignature,
            signature,
            startedAt: confirmStartedAt,
            success: true,
            details: { mode: "background", executor: "rust" },
          });
          args.onConfirmSuccess?.(args.mint, Number(quote.tokensIn), Number(quote.solOut), signature);
        })
        .catch((err) => {
          const msg = err instanceof Error ? err.message : String(err);
          recordConfirmStage({
            route: useSellV2 ? "pumpfun-direct-sell-v2" : "pumpfun-direct-sell",
            side: "sell",
            mint: args.mint,
            sourceSignature: args.sourceSignature,
            signature,
            startedAt: confirmStartedAt,
            success: false,
            error: msg,
            details: { mode: "background", executor: "rust" },
          });
          args.onConfirmFailure?.(args.mint, Number(quote.tokensIn), Number(quote.solOut), msg);
        });
      return {
        ok: true,
        signature,
        isSimulated: false,
        tokensIn: quote.tokensIn,
        solOut: quote.solOut,
        priceImpactPct: quote.priceImpactPct,
        mintMeta: await getTokenMeta(args.mint),
        confirmationPending: true,
      };
    }
    const confirmStartedAt = Date.now();
    try {
      await confirmSignature(signature);
      recordConfirmStage({
        route: useSellV2 ? "pumpfun-direct-sell-v2" : "pumpfun-direct-sell",
        side: "sell",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature,
        startedAt: confirmStartedAt,
        success: true,
        details: { mode: "blocking", executor: "rust" },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      recordConfirmStage({
        route: useSellV2 ? "pumpfun-direct-sell-v2" : "pumpfun-direct-sell",
        side: "sell",
        mint: args.mint,
        sourceSignature: args.sourceSignature,
        signature,
        startedAt: confirmStartedAt,
        success: false,
        error: msg,
        details: { mode: "blocking", executor: "rust" },
      });
      return { ok: false, recoverable: false, reason: `Pump.fun Rust sell confirmation failed: ${msg}` };
    }
    return {
      ok: true,
      signature,
      isSimulated: false,
      tokensIn: quote.tokensIn,
      solOut: quote.solOut,
      priceImpactPct: quote.priceImpactPct,
      mintMeta: await getTokenMeta(args.mint),
    };
  }
  if (rustOnlySells) {
    const reason = rust && !rust.ok ? rust.error : "Rust Pump.fun sell executor unavailable";
    args.log(`Rust-only Pump.fun sell failed; TypeScript fallback disabled: ${reason}`);
    return { ok: false, recoverable: false, reason: `Rust-only Pump.fun sell failed: ${reason}` };
  }
  if (rust && !rust.ok) {
    args.log(`Rust executor Pump.fun sell failed; falling back to TypeScript: ${rust.error}`);
  }
  let tx;
  try {
    tx = await buildPumpFunSellTransaction({
    user: args.user,
    mint: mintKey,
    bondingCurve: curve.pda,
    creator: curve.state.creator,
    tokenProgramId: curve.tokenProgramId,
    tokensIn: args.tokensInRaw,
    expectedSolOut: quote.solOut,
    slippageBps: args.slippageBps,
    remainingAccounts,
    useV2: useSellV2,
    buybackFeeRecipient: useSellV2 ? PUMP_FUN_BUYBACK_FEE_RECIPIENT : undefined,
    });
    recordExecutionStage({
      stage: "build",
      route: "pumpfun-direct-sell",
      side: "sell",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: Date.now() - buildStartedAt,
      success: true,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    recordExecutionStage({
      stage: "build",
      route: "pumpfun-direct-sell",
      side: "sell",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      elapsedMs: Date.now() - buildStartedAt,
      success: false,
      error: msg,
    });
    return { ok: false, recoverable: true, reason: msg };
  }
  args.log(`Tx built (${elapsed()})`);

  let signature: string;
  try {
    signature = await broadcastTransactionWithSender(tx, {
      preferHelius: (args.rebroadcastAttempt ?? 0) > 0,
      route: "pumpfun-direct-sell",
      side: "sell",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, recoverable: false, reason: `broadcast failed: ${msg}` };
  }
  args.log(`Broadcast: ${signature} (${elapsed()})`);

  if (fastMode) {
    // Fast mode keeps the manual/risk exit hot; any late failure is surfaced by the background poller.
    const confirmStartedAt = Date.now();
    confirmSignature(signature)
      .then(() => {
        recordConfirmStage({
          route: "pumpfun-direct-sell",
          side: "sell",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          signature,
          startedAt: confirmStartedAt,
          success: true,
          details: { mode: "background" },
        });
        args.onConfirmSuccess?.(args.mint, Number(quote.tokensIn), Number(quote.solOut), signature);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
        recordConfirmStage({
          route: "pumpfun-direct-sell",
          side: "sell",
          mint: args.mint,
          sourceSignature: args.sourceSignature,
          signature,
          startedAt: confirmStartedAt,
          success: false,
          error: msg,
          details: { mode: "background" },
        });
        console.warn(`Pump.fun direct sell confirmation failed after broadcast: ${msg}`);
        args.onConfirmFailure?.(args.mint, Number(quote.tokensIn), Number(quote.solOut), msg);
      });
    const mintMeta = await getTokenMeta(args.mint);
    return {
      ok: true,
      signature,
      isSimulated: false,
      tokensIn: quote.tokensIn,
      solOut: quote.solOut,
      priceImpactPct: quote.priceImpactPct,
      mintMeta,
      confirmationPending: true,
    };
  }
  const confirmStartedAt = Date.now();
  try {
    await confirmSignature(signature);
    recordConfirmStage({
      route: "pumpfun-direct-sell",
      side: "sell",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      signature,
      startedAt: confirmStartedAt,
      success: true,
      details: { mode: "blocking" },
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    recordConfirmStage({
      route: "pumpfun-direct-sell",
      side: "sell",
      mint: args.mint,
      sourceSignature: args.sourceSignature,
      signature,
      startedAt: confirmStartedAt,
      success: false,
      error: msg,
      details: { mode: "blocking", rebroadcastAttempt: args.rebroadcastAttempt ?? 0 },
    });
    const rebroadcastAttempt = args.rebroadcastAttempt ?? 0;
    if (isConfirmationTimeoutError(err) && rebroadcastAttempt < confirmationRebroadcastRetries) {
      // A dropped sell is worse than a delayed one; retry once with a fresh quote/blockhash.
      args.log(`Pump.fun sell suspected dropped (${msg}); rebuilding once with fresh blockhash...`);
      return tryPumpFunDirectSell({
        ...args,
        rebroadcastAttempt: rebroadcastAttempt + 1,
      });
    }
    if (isPumpFunSellUnsupportedTokenProgram(err)) {
      return {
        ok: false,
        recoverable: true,
        reason: "pump.fun direct sell rejected account layout/fee routing",
      };
    }
    throw err;
  }
  args.log(`Confirmed (${elapsed()})`);

  const mintMeta = await getTokenMeta(args.mint);
  return {
    ok: true,
    signature,
    isSimulated: false,
    tokensIn: quote.tokensIn,
    solOut: quote.solOut,
    priceImpactPct: quote.priceImpactPct,
    mintMeta,
  };
}

/**
 * Lightweight valuation helper for the risk loop: read the curve and compute expected SOL out
 * for a given held token amount. Returns null if the token isn't on a pump.fun curve OR has
 * graduated — the caller should fall back to Jupiter quote in those cases.
 */
export async function valueOnCurve(
  mint: string,
  tokensInRaw: number
): Promise<{ solOut: number; priceImpactPct: number } | null> {
  if (tokensInRaw <= 0) return null;
  try {
    const curve = await getBondingCurveState(new PublicKey(mint));
    if (!curve || curve.state.complete) return null;
    const q = quoteSellOnCurve(curve.state, BigInt(tokensInRaw));
    return { solOut: Number(q.solOut), priceImpactPct: q.priceImpactPct };
  } catch {
    return null;
  }
}
