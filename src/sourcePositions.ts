import type { TokenMeta } from "./tokenRegistry.js";

export type SourcePosition = {
  mint: string;
  symbol: string;
  decimals: number;
  totalBoughtRaw: number;
  totalSoldRaw: number;
  buysCount: number;
  sellsCount: number;
  firstSeenAt: number;
  lastTradeAt: number;
};

const sourcePositions = new Map<string, SourcePosition>();

function ensure(mint: string, meta: TokenMeta): SourcePosition {
  let p = sourcePositions.get(mint);
  if (!p) {
    const now = Date.now();
    p = {
      mint,
      symbol: meta.symbol,
      decimals: meta.decimals,
      totalBoughtRaw: 0,
      totalSoldRaw: 0,
      buysCount: 0,
      sellsCount: 0,
      firstSeenAt: now,
      lastTradeAt: now,
    };
    sourcePositions.set(mint, p);
  } else {
    // Keep metadata fresh
    p.symbol = meta.symbol;
    p.decimals = meta.decimals;
  }
  return p;
}

export function recordSourceBuy(
  tokenMeta: TokenMeta,
  tokenAmountRaw: number
): SourcePosition {
  const p = ensure(tokenMeta.mint, tokenMeta);
  p.totalBoughtRaw += tokenAmountRaw;
  p.buysCount++;
  p.lastTradeAt = Date.now();
  return p;
}

export function recordSourceSell(
  tokenMeta: TokenMeta,
  tokenAmountRaw: number
): SourcePosition {
  const p = ensure(tokenMeta.mint, tokenMeta);
  p.totalSoldRaw += tokenAmountRaw;
  p.sellsCount++;
  p.lastTradeAt = Date.now();
  return p;
}

export function getSourcePosition(mint: string): SourcePosition | null {
  return sourcePositions.get(mint) ?? null;
}

export function getSourceHeldRaw(mint: string): number {
  const p = sourcePositions.get(mint);
  if (!p) return 0;
  return Math.max(p.totalBoughtRaw - p.totalSoldRaw, 0);
}

export function calculateProportionalSell(
  sourceSellAmountRaw: number,
  sourceMint: string,
  ourHeldRaw: number
): number {
  const sourceHeldRaw = getSourceHeldRaw(sourceMint);
  
  // If source has no holdings recorded, sell the same amount they're selling (capped by our holdings)
  if (sourceHeldRaw <= 0) {
    return Math.min(sourceSellAmountRaw, ourHeldRaw);
  }
  
  // Calculate what percentage of source's holdings this sell represents
  const sellPercentage = sourceSellAmountRaw / sourceHeldRaw;
  
  // Apply same percentage to our holdings
  const proportionalSell = Math.round(ourHeldRaw * sellPercentage);
  
  // Ensure we don't try to sell more than we have
  return Math.min(proportionalSell, ourHeldRaw);
}

export function clearSourcePositions(): void {
  sourcePositions.clear();
}
