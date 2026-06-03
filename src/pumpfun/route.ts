import { Keypair, PublicKey, type AccountMeta } from "@solana/web3.js";
import { broadcastTransactionWithSender } from "../sender.js";
import { confirmSignature, isConfirmationTimeoutError } from "../confirm.js";
import {
  confirmationRebroadcastRetries,
  fastMode,
  simulationMode,
  mirrorRetrySlippageBps,
  readConnection,
  usePumpfunToken2022DirectSell,
} from "../config.js";
import { getTokenMeta, type TokenMeta, WRAPPED_SOL_MINT } from "../tokenRegistry.js";
import {
  PUMP_FUN_PROGRAM_ID,
  PUMP_FUN_BUYBACK_FEE_RECIPIENT,
  TOKEN_2022_PROGRAM_ID,
} from "./constants.js";
import {
  getBondingCurveState,
  quoteBuyOnCurve,
  quoteSellOnCurve,
  maxSolForImpact,
} from "./curve.js";
import { buildPumpFunBuyReplayTransaction, buildPumpFunBuyTransaction } from "./buy.js";
import { buildPumpFunSellTransaction } from "./sell.js";

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
  const curve = await getBondingCurveState(mintKey);
  if (!curve) {
    if (args.sourceIxAccounts && args.sourceIxAccounts.length >= 16 && args.sourceIxDataBase64) {
      args.log("Pump.fun curve RPC unavailable; replaying scaled source Pump.fun ix.");
      const sourceIxPubkeys = args.sourceIxAccounts.map((k) => new PublicKey(k));
      const sourceIxData = Buffer.from(args.sourceIxDataBase64, "base64");
      const solIn = BigInt(args.solInLamports);
      if (simulationMode) {
        const mintMeta = await getTokenMeta(args.mint);
        return {
          ok: true,
          signature: `sim-pumpfun-replay-${args.mint.slice(0, 8)}-${Date.now()}`,
          isSimulated: true,
          tokensOut: 1n,
          solIn,
          priceImpactPct: 0,
          mintMeta,
        };
      }
      args.log("Building pump.fun source-replay buy tx...");
      let signature: string;
      let estimatedTokensOut: bigint;
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
        return { ok: false, recoverable: false, reason: `source-replay failed: ${msg}` };
      }
      args.log(`Broadcast: ${signature} (${elapsed()})`);
      if (fastMode) {
        confirmSignature(signature)
          .then(() => {
            args.onConfirmSuccess?.(args.mint, Number(estimatedTokensOut), Number(solIn), signature);
          })
          .catch((err) => {
            const reason = err instanceof Error ? err.message : String(err);
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
      await confirmSignature(signature);
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
  // Prefer source ix accounts from the gRPC payload (always fresh, never races confirmation).
  // Fall back to the getTransaction-based extraction for non-gRPC source signals.
  let sourceIxPubkeys: PublicKey[] | undefined;
  if (args.sourceIxAccounts && args.sourceIxAccounts.length >= 16) {
    sourceIxPubkeys = args.sourceIxAccounts.map((k) => new PublicKey(k));
    args.log(`Using ${sourceIxPubkeys.length} source pump.fun account(s) from gRPC payload.`);
  }
  const remainingAccounts = sourceIxPubkeys ? [] : await sourceAccountsPromise;
  if (!sourceIxPubkeys && remainingAccounts.length > 0) {
    args.log(`Using ${remainingAccounts.length} source pump.fun remaining account(s) from getTransaction.`);
  }
  const tx = await buildPumpFunBuyTransaction({
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
  args.log(`Tx built (${elapsed()})`);

  let signature: string;
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
  args.log(`Broadcast: ${signature} (${elapsed()})`);

  if (fastMode) {
    // Fast mode records after broadcast so snipes are not delayed by confirmation polling.
    confirmSignature(signature)
      .then(() => {
        args.onConfirmSuccess?.(args.mint, Number(quote.tokensOut), Number(quote.solIn), signature);
      })
      .catch((err) => {
        const reason = err instanceof Error ? err.message : String(err);
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
  try {
    await confirmSignature(signature);
  } catch (err) {
    const rebroadcastAttempt = args.rebroadcastAttempt ?? 0;
    if (isConfirmationTimeoutError(err) && rebroadcastAttempt < confirmationRebroadcastRetries) {
      const msg = err instanceof Error ? err.message : String(err);
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
  const tx = await buildPumpFunSellTransaction({
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
    confirmSignature(signature)
      .then(() => {
        args.onConfirmSuccess?.(args.mint, Number(quote.tokensIn), Number(quote.solOut), signature);
      })
      .catch((err) => {
        const msg = err instanceof Error ? err.message : String(err);
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
  try {
    await confirmSignature(signature);
  } catch (err) {
    const rebroadcastAttempt = args.rebroadcastAttempt ?? 0;
    if (isConfirmationTimeoutError(err) && rebroadcastAttempt < confirmationRebroadcastRetries) {
      const msg = err instanceof Error ? err.message : String(err);
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
