import { PublicKey } from "@solana/web3.js";
import { readConnection } from "../config.js";
import {
  PUMP_FUN_PROGRAM_ID,
  PUMP_FUN_TRADE_FEE_BPS,
  TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
  deriveBondingCurvePda,
} from "./constants.js";

/**
 * Bonding curve account layout (after the 8-byte anchor discriminator):
 *   virtual_token_reserves: u64    (offset 8)
 *   virtual_sol_reserves:   u64    (offset 16)
 *   real_token_reserves:    u64    (offset 24)
 *   real_sol_reserves:      u64    (offset 32)
 *   token_total_supply:     u64    (offset 40)
 *   complete:               bool   (offset 48)
 *
 * Newer pump.fun versions append more fields (creator, etc.). We only read the first 49 bytes.
 */
export type BondingCurveState = {
  virtualTokenReserves: bigint;
  virtualSolReserves: bigint;
  realTokenReserves: bigint;
  realSolReserves: bigint;
  tokenTotalSupply: bigint;
  complete: boolean;
  creator: PublicKey | null;
};

function readU64LE(buf: Buffer, offset: number): bigint {
  // Buffer.readBigUInt64LE exists in Node — use it for safety on large values.
  return buf.readBigUInt64LE(offset);
}

export function decodeBondingCurve(data: Buffer): BondingCurveState | null {
  if (data.length < 49) return null;
  return {
    virtualTokenReserves: readU64LE(data, 8),
    virtualSolReserves: readU64LE(data, 16),
    realTokenReserves: readU64LE(data, 24),
    realSolReserves: readU64LE(data, 32),
    tokenTotalSupply: readU64LE(data, 40),
    complete: data[48] === 1,
    creator: data.length >= 81 ? new PublicKey(data.subarray(49, 81)) : null,
  };
}

/**
 * Fetch + decode the bonding curve for a mint, AND detect which SPL token program owns the mint.
 * Returns null if the curve account doesn't exist, isn't pump.fun, or is malformed, OR if the mint
 * is owned by an unrecognized token program. Token program detection matters because pump.fun mints
 * use both classic SPL Token (older) and Token-2022 (newer) — ATA derivation and instruction account
 * lists need the right one or the tx fails with IncorrectProgramId.
 */
export async function getBondingCurveState(
  mint: PublicKey
): Promise<{
  pda: PublicKey;
  state: BondingCurveState;
  tokenProgramId: PublicKey;
} | null> {
  const pda = deriveBondingCurvePda(mint);
  // Fetch curve and mint in parallel — both reads cost ~80-150ms; serial would be wasteful.
  const [curveAcct, mintAcct] = await Promise.all([
    readConnection.getAccountInfo(pda, "confirmed"),
    readConnection.getAccountInfo(mint, "confirmed"),
  ]);
  if (!curveAcct || !curveAcct.owner.equals(PUMP_FUN_PROGRAM_ID)) return null;
  if (!mintAcct) return null;
  // Token program = whoever owns the mint account.
  const tokenProgramId = mintAcct.owner;
  if (!tokenProgramId.equals(TOKEN_PROGRAM_ID) && !tokenProgramId.equals(TOKEN_2022_PROGRAM_ID)) {
    // Unknown token program — refuse rather than guess.
    return null;
  }
  const state = decodeBondingCurve(curveAcct.data);
  if (!state) return null;
  return { pda, state, tokenProgramId };
}

/**
 * Compute expected tokens out for a SOL buy, including the trade fee. Uses the constant-product
 * invariant `k = virtual_sol × virtual_tokens` against post-fee SOL added to virtual_sol_reserves.
 *
 * Returns:
 *   tokensOut: tokens received (raw, with mint decimals)
 *   solIn:     SOL spent in lamports (== requested input)
 *   feeLamports: trade fee deducted before curve math
 *   newSolReserves / newTokenReserves: post-trade virtual reserves (for impact computation)
 *   priceImpactPct: (post_price - pre_price) / pre_price × 100
 */
export type BuyQuote = {
  tokensOut: bigint;
  solIn: bigint;
  feeLamports: bigint;
  preSolReserves: bigint;
  preTokenReserves: bigint;
  postSolReserves: bigint;
  postTokenReserves: bigint;
  priceImpactPct: number;
};

export function quoteBuyOnCurve(state: BondingCurveState, solInLamports: bigint): BuyQuote {
  if (solInLamports <= 0n) {
    throw new Error("solInLamports must be positive");
  }
  if (state.complete) {
    throw new Error("Bonding curve already complete (token graduated)");
  }

  const fee = (solInLamports * BigInt(PUMP_FUN_TRADE_FEE_BPS)) / 10_000n;
  const solAfterFee = solInLamports - fee;

  const preSol = state.virtualSolReserves;
  const preTok = state.virtualTokenReserves;
  const k = preSol * preTok;

  const newSol = preSol + solAfterFee;
  const newTok = k / newSol; // bigint division floors — slightly conservative
  const tokensOut = preTok - newTok;

  // Pre-trade price (lamports per token raw): preSol / preTok
  // Post-trade price: newSol / newTok
  // Use floats for the impact % since we're done with exactness.
  const prePrice = Number(preSol) / Number(preTok);
  const postPrice = Number(newSol) / Number(newTok);
  const priceImpactPct = prePrice > 0 ? ((postPrice - prePrice) / prePrice) * 100 : 0;

  return {
    tokensOut,
    solIn: solInLamports,
    feeLamports: fee,
    preSolReserves: preSol,
    preTokenReserves: preTok,
    postSolReserves: newSol,
    postTokenReserves: newTok,
    priceImpactPct,
  };
}

/**
 * Compute expected SOL out for a sell, including the trade fee. Inverse constant-product:
 * tokens_in raises virtual_token_reserves, virtual_sol_reserves drops, fee deducted from SOL output.
 *
 *   k          = pre_sol × pre_tok
 *   new_tok    = pre_tok + tokens_in
 *   new_sol    = k / new_tok
 *   sol_gross  = pre_sol − new_sol
 *   fee        = sol_gross × fee_bps / 10000
 *   sol_net    = sol_gross − fee
 */
export type SellQuote = {
  solOut: bigint;
  feeLamports: bigint;
  tokensIn: bigint;
  preSolReserves: bigint;
  preTokenReserves: bigint;
  postSolReserves: bigint;
  postTokenReserves: bigint;
  /** Always reported as a positive percentage (the absolute price drop). */
  priceImpactPct: number;
};

export function quoteSellOnCurve(state: BondingCurveState, tokensInRaw: bigint): SellQuote {
  if (tokensInRaw <= 0n) {
    throw new Error("tokensInRaw must be positive");
  }
  if (state.complete) {
    throw new Error("Bonding curve already complete (token graduated)");
  }

  const preSol = state.virtualSolReserves;
  const preTok = state.virtualTokenReserves;
  const k = preSol * preTok;

  const newTok = preTok + tokensInRaw;
  const newSol = k / newTok;
  const solGross = preSol - newSol;
  const fee = (solGross * BigInt(PUMP_FUN_TRADE_FEE_BPS)) / 10_000n;
  const solOut = solGross - fee;

  const prePrice = Number(preSol) / Number(preTok);
  const postPrice = Number(newSol) / Number(newTok);
  const priceImpactPct = prePrice > 0 ? Math.abs((postPrice - prePrice) / prePrice) * 100 : 0;

  return {
    solOut,
    feeLamports: fee,
    tokensIn: tokensInRaw,
    preSolReserves: preSol,
    preTokenReserves: preTok,
    postSolReserves: newSol,
    postTokenReserves: newTok,
    priceImpactPct,
  };
}

/**
 * Inverse: given a target impact %, compute the maximum SOL we can spend without exceeding it.
 * Used by liquidity-aware sizing (size to fit cap, don't multi-probe).
 *
 * Approximate solve via algebra:
 *   target_impact = (post/pre - 1) × 100
 *   post = preSol/preTok × (1 + impact/100)... actually that's price, not amount
 *
 * Simpler: binary-search since the math is monotonic and cheap.
 */
export function maxSolForImpact(state: BondingCurveState, maxImpactPct: number): bigint {
  if (maxImpactPct <= 0) return 0n;
  // Upper bound: virtual_sol_reserves itself (would 100% the curve, nonsensical but bounds the search).
  let lo = 0n;
  let hi = state.virtualSolReserves;
  // Need ~30 iterations for ~1e-9 precision in lamports — overkill but cheap.
  for (let i = 0; i < 40; i++) {
    const mid = (lo + hi) / 2n;
    if (mid === lo) break;
    try {
      const q = quoteBuyOnCurve(state, mid);
      if (q.priceImpactPct <= maxImpactPct) lo = mid;
      else hi = mid;
    } catch {
      hi = mid;
    }
  }
  return lo;
}
