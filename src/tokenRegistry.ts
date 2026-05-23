import { PublicKey } from "@solana/web3.js";
import { readConnection, readRpcUrl } from "./config.js";

export type TokenMeta = {
  mint: string;
  symbol: string;
  name: string;
  decimals: number;
  isSol: boolean;
};

export const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
export const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const FETCH_TIMEOUT_MS = 3000;

const cache = new Map<string, TokenMeta>();
const inflight = new Map<string, Promise<TokenMeta>>();

cache.set(WRAPPED_SOL_MINT, {
  mint: WRAPPED_SOL_MINT,
  symbol: "SOL",
  name: "Solana",
  decimals: 9,
  isSol: true,
});
cache.set(USDC_MINT, {
  mint: USDC_MINT,
  symbol: "USDC",
  name: "USD Coin",
  decimals: 6,
  isSol: false,
});

type Partial = { symbol?: string; name?: string; decimals?: number };

async function fetchHeliusDAS(mint: string): Promise<Partial | null> {
  try {
    const res = await fetch(readRpcUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: "getAsset",
        method: "getAsset",
        params: { id: mint },
      }),
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!res.ok) return null;
    const json = (await res.json()) as {
      result?: {
        content?: { metadata?: { name?: string; symbol?: string } };
        token_info?: { decimals?: number; symbol?: string };
      };
    };
    const r = json.result;
    if (!r) return null;
    const symbol = r.content?.metadata?.symbol ?? r.token_info?.symbol;
    const name = r.content?.metadata?.name;
    const decimals = r.token_info?.decimals;
    return {
      symbol: typeof symbol === "string" && symbol.length ? symbol : undefined,
      name: typeof name === "string" && name.length ? name : undefined,
      decimals: typeof decimals === "number" ? decimals : undefined,
    };
  } catch {
    return null;
  }
}

async function fetchDexScreener(mint: string): Promise<Partial | null> {
  try {
    const res = await fetch(
      `https://api.dexscreener.com/latest/dex/tokens/${mint}`,
      { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) }
    );
    if (!res.ok) return null;
    const json = (await res.json()) as {
      pairs?: Array<{
        baseToken?: { address?: string; symbol?: string; name?: string };
        quoteToken?: { address?: string; symbol?: string; name?: string };
      }>;
    };
    const pair = json.pairs?.[0];
    if (!pair) return null;
    const tok =
      pair.baseToken?.address === mint
        ? pair.baseToken
        : pair.quoteToken?.address === mint
        ? pair.quoteToken
        : null;
    if (!tok) return null;
    return { symbol: tok.symbol, name: tok.name };
  } catch {
    return null;
  }
}

/** Fetch market cap from PumpPortal API (pump.fun specific) */
async function getPumpFunMarketCap(mint: string): Promise<number | null> {
  try {
    const res = await fetch(
      `https://pumpportal.fun/api/token/${mint}`,
      { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) }
    );
    if (!res.ok) return null;
    
    const json = (await res.json()) as { market_cap?: number };
    return typeof json.market_cap === "number" && json.market_cap > 0 ? json.market_cap : null;
  } catch {
    return null;
  }
}

/** Fetch market cap from DexScreener API (fallback) */
async function getDexScreenerMarketCap(mint: string): Promise<number | null> {
  try {
    const res = await fetch(
      `https://api.dexscreener.com/latest/dex/tokens/${mint}`,
      { signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) }
    );
    if (!res.ok) return null;
    
    const json = (await res.json()) as {
      pairs?: Array<{
        marketCap?: number;
        fdv?: number;
        liquidity?: { usd?: number };
      }>;
    };
    const pairs = json.pairs ?? [];
    if (pairs.length === 0) return null;
    
    // Prefer the pair with the most liquidity (best price discovery).
    const best = pairs.reduce((a, b) =>
      (b.liquidity?.usd ?? 0) > (a.liquidity?.usd ?? 0) ? b : a
    );
    const mc = best.marketCap ?? best.fdv;
    return typeof mc === "number" && mc > 0 ? mc : null;
  } catch {
    return null;
  }
}

/** Fetch live market cap (USD) for a mint. Returns null if unavailable. 
 *  Tries PumpPortal first (pump.fun tokens), then DexScreener as fallback.
 */
export async function getTokenMarketCap(mint: string): Promise<number | null> {
  let lastError: unknown;
  
  // Try PumpPortal first (most reliable for pump.fun tokens)
  try {
    const pumpFunMC = await getPumpFunMarketCap(mint);
    if (pumpFunMC !== null) {
      return pumpFunMC;
    }
  } catch (err) {
    lastError = err;
  }
  
  // Fallback to DexScreener
  try {
    const dexScreenerMC = await getDexScreenerMarketCap(mint);
    if (dexScreenerMC !== null) {
      return dexScreenerMC;
    }
  } catch (err) {
    lastError = err;
  }
  
  // Log debug info for troubleshooting
  console.warn(`?? MC not available for ${mint.slice(0, 8)}? (tried PumpPortal + DexScreener)`);
  
  return null;
}

export function formatMarketCap(mc: number): string {
  if (mc >= 1_000_000_000) return `$${(mc / 1_000_000_000).toFixed(2)}B`;
  if (mc >= 1_000_000) return `$${(mc / 1_000_000).toFixed(2)}M`;
  if (mc >= 1_000) return `$${(mc / 1_000).toFixed(1)}K`;
  return `$${mc.toFixed(0)}`;
}

async function fetchMintDecimals(mint: string): Promise<number | null> {
  try {
    const info = await readConnection.getParsedAccountInfo(new PublicKey(mint));
    const parsed = info.value?.data as
      | { parsed?: { info?: { decimals?: number } } }
      | undefined;
    const d = parsed?.parsed?.info?.decimals;
    return typeof d === "number" ? d : null;
  } catch {
    return null;
  }
}

async function load(mint: string): Promise<TokenMeta> {
  const helius = await fetchHeliusDAS(mint);
  let symbol = helius?.symbol;
  let name = helius?.name;
  let decimals = helius?.decimals;

  if (!symbol || !name) {
    const dex = await fetchDexScreener(mint);
    symbol = symbol ?? dex?.symbol;
    name = name ?? dex?.name;
  }

  if (decimals == null) {
    const d = await fetchMintDecimals(mint);
    if (d != null) decimals = d;
  }

  const shortMint = `${mint.slice(0, 4)}…${mint.slice(-4)}`;
  const meta: TokenMeta = {
    mint,
    symbol: symbol && symbol.length ? symbol : shortMint,
    name: name && name.length ? name : symbol ?? shortMint,
    decimals: decimals ?? 0,
    isSol: false,
  };
  cache.set(mint, meta);
  return meta;
}

export async function getTokenMeta(mint: string): Promise<TokenMeta> {
  const existing = cache.get(mint);
  if (existing) return existing;
  const pending = inflight.get(mint);
  if (pending) return pending;
  const p = load(mint).finally(() => inflight.delete(mint));
  inflight.set(mint, p);
  return p;
}

export function shortMint(mint: string): string {
  if (!mint) return "";
  return mint.length > 10 ? `${mint.slice(0, 4)}…${mint.slice(-4)}` : mint;
}

export function formatTokenAmount(rawAmount: number, decimals: number): string {
  if (!Number.isFinite(rawAmount)) return "0";
  if (decimals === 0) return Math.round(rawAmount).toLocaleString("en-US");
  const value = rawAmount / Math.pow(10, decimals);
  const maxFrac = value >= 1000 ? 2 : value >= 1 ? 4 : Math.min(decimals, 6);
  return value.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: maxFrac,
  });
}

export function formatSolAmount(lamports: number): string {
  const sol = lamports / 1_000_000_000;
  const frac = Math.abs(sol) >= 100 ? 2 : Math.abs(sol) >= 1 ? 3 : 4;
  return sol.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: frac,
  });
}
