import { Keypair, PublicKey } from "@solana/web3.js";
import { broadcastTransactionWithSender } from "../sender.js";
import { confirmSignature } from "../confirm.js";
import { fastMode, simulationMode, mirrorSlippageBps } from "../config.js";
import { getTokenMeta, type TokenMeta, WRAPPED_SOL_MINT } from "../tokenRegistry.js";
import {
  getBondingCurveState,
  quoteBuyOnCurve,
  quoteSellOnCurve,
  maxSolForImpact,
} from "./curve.js";
import { buildPumpFunBuyTransaction } from "./buy.js";
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
    }
  | { ok: false; reason: string; recoverable: boolean };

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
  /**
   * Called only in fastMode if background confirmation reports the buy failed on chain.
   * Caller should roll back the recorded position so the risk loop doesn't keep trying to sell
   * tokens we never actually received.
   */
  onConfirmFailure?: (mint: string, tokensOutRaw: number, solInLamports: number, reason: string) => void;
}): Promise<PumpFunBuyOutcome> {
  const elapsed = () => `${Date.now() - args.startedAt}ms`;
  const mintKey = new PublicKey(args.mint);

  // 1. Detect: is this a pump.fun bonding-curve token, still pre-graduation?
  const curve = await getBondingCurveState(mintKey);
  if (!curve) {
    return { ok: false, recoverable: true, reason: "not a pump.fun bonding-curve token" };
  }
  if (curve.state.complete) {
    return { ok: false, recoverable: true, reason: "pump.fun token has graduated" };
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
  const tx = await buildPumpFunBuyTransaction({
    user: args.user,
    mint: mintKey,
    bondingCurve: curve.pda,
    tokenProgramId: curve.tokenProgramId,
    tokensOut: quote.tokensOut,
    solInLamports: quote.solIn,
    slippageBps: args.slippageBps,
  });
  args.log(`Tx built (${elapsed()})`);

  let signature: string;
  try {
    signature = await broadcastTransactionWithSender(tx);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, recoverable: false, reason: `broadcast failed: ${msg}` };
  }
  args.log(`Broadcast: ${signature} (${elapsed()})`);

  if (fastMode) {
    const tokensOut = quote.tokensOut;
    const solIn = quote.solIn;
    const onFail = args.onConfirmFailure;
    void confirmSignature(signature)
      .then(() => console.log(`Pump.fun buy confirmed in background: ${signature}`))
      .catch((e) => {
        const m = e instanceof Error ? e.message : String(e);
        console.warn(`Pump.fun buy bg-confirm failed for ${signature}: ${m}`);
        if (onFail) {
          try {
            onFail(args.mint, Number(tokensOut), Number(solIn), m);
          } catch (cbErr) {
            const cbMsg = cbErr instanceof Error ? cbErr.message : String(cbErr);
            console.warn(`onConfirmFailure callback errored: ${cbMsg}`);
          }
        }
      });
  } else {
    await confirmSignature(signature);
    args.log(`Confirmed (${elapsed()})`);
  }

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
  const tx = await buildPumpFunSellTransaction({
    user: args.user,
    mint: mintKey,
    bondingCurve: curve.pda,
    tokenProgramId: curve.tokenProgramId,
    tokensIn: args.tokensInRaw,
    expectedSolOut: quote.solOut,
    slippageBps: args.slippageBps,
  });
  args.log(`Tx built (${elapsed()})`);

  let signature: string;
  try {
    signature = await broadcastTransactionWithSender(tx);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { ok: false, recoverable: false, reason: `broadcast failed: ${msg}` };
  }
  args.log(`Broadcast: ${signature} (${elapsed()})`);

  if (fastMode) {
    void confirmSignature(signature)
      .then(() => console.log(`Pump.fun sell confirmed in background: ${signature}`))
      .catch((e) => {
        const m = e instanceof Error ? e.message : String(e);
        console.warn(`Pump.fun sell bg-confirm failed for ${signature}: ${m}`);
      });
  } else {
    await confirmSignature(signature);
    args.log(`Confirmed (${elapsed()})`);
  }

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
