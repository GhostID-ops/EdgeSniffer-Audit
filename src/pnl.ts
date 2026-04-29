import type { TokenMeta } from "./tokenRegistry.js";

export type Position = {
  mint: string;
  symbol: string;
  decimals: number;
  totalBoughtRaw: number;
  totalSolSpentLamports: number;
  totalSoldRaw: number;
  totalSolReceivedLamports: number;
  buysCount: number;
  sellsCount: number;
  firstSeenAt: number;
  lastTradeAt: number;
};

export type PositionSnapshot = Position & {
  heldRaw: number;
  avgCostLamportsPerTokenRaw: number | null;
  realizedPnlLamports: number;
  costBasisRemainingLamports: number;
};

const positions = new Map<string, Position>();

function ensure(mint: string, meta: TokenMeta): Position {
  let p = positions.get(mint);
  if (!p) {
    const now = Date.now();
    p = {
      mint,
      symbol: meta.symbol,
      decimals: meta.decimals,
      totalBoughtRaw: 0,
      totalSolSpentLamports: 0,
      totalSoldRaw: 0,
      totalSolReceivedLamports: 0,
      buysCount: 0,
      sellsCount: 0,
      firstSeenAt: now,
      lastTradeAt: now,
    };
    positions.set(mint, p);
  } else {
    // Keep metadata fresh in case the registry got better data later.
    p.symbol = meta.symbol;
    p.decimals = meta.decimals;
  }
  return p;
}

export function recordBuy(
  tokenMeta: TokenMeta,
  tokenAmountRaw: number,
  solLamports: number
): PositionSnapshot {
  const p = ensure(tokenMeta.mint, tokenMeta);
  p.totalBoughtRaw += tokenAmountRaw;
  p.totalSolSpentLamports += solLamports;
  p.buysCount++;
  p.lastTradeAt = Date.now();
  return snapshot(p);
}

export function recordSell(
  tokenMeta: TokenMeta,
  tokenAmountRaw: number,
  solLamports: number
): PositionSnapshot {
  const p = ensure(tokenMeta.mint, tokenMeta);
  p.totalSoldRaw += tokenAmountRaw;
  p.totalSolReceivedLamports += solLamports;
  p.sellsCount++;
  p.lastTradeAt = Date.now();
  return snapshot(p);
}

function snapshot(p: Position): PositionSnapshot {
  const avgCost =
    p.totalBoughtRaw > 0 ? p.totalSolSpentLamports / p.totalBoughtRaw : null;
  const soldCostBasis = avgCost != null ? avgCost * p.totalSoldRaw : 0;
  const realizedPnl = p.totalSolReceivedLamports - soldCostBasis;
  const held = p.totalBoughtRaw - p.totalSoldRaw;
  const remainingCost = avgCost != null ? avgCost * Math.max(held, 0) : 0;
  return {
    ...p,
    heldRaw: held,
    avgCostLamportsPerTokenRaw: avgCost,
    realizedPnlLamports: realizedPnl,
    costBasisRemainingLamports: remainingCost,
  };
}

export function getPositions(): PositionSnapshot[] {
  return Array.from(positions.values())
    .map(snapshot)
    .sort((a, b) => b.lastTradeAt - a.lastTradeAt);
}

export function getPosition(mint: string): PositionSnapshot | null {
  const p = positions.get(mint);
  return p ? snapshot(p) : null;
}

export function markPositionClosedAsDust(mint: string): PositionSnapshot | null {
  const p = positions.get(mint);
  if (!p) return null;

  const heldRaw = p.totalBoughtRaw - p.totalSoldRaw;
  if (heldRaw > 0) {
    p.totalSoldRaw += heldRaw;
    p.sellsCount++;
    p.lastTradeAt = Date.now();
  }

  return snapshot(p);
}

/** Clear all tracked positions. Used by the "reset" keyboard shortcut. */
export function resetPositions(): void {
  positions.clear();
}

export type PnlTotals = {
  buysCount: number;
  sellsCount: number;
  tradesCount: number;
  totalSolSpentLamports: number;
  totalSolReceivedLamports: number;
  realizedPnlLamports: number;
  openPositions: number;
  trackedTokens: number;
};

export function getTotals(): PnlTotals {
  let buys = 0;
  let sells = 0;
  let spent = 0;
  let received = 0;
  let pnl = 0;
  let open = 0;
  for (const p of positions.values()) {
    buys += p.buysCount;
    sells += p.sellsCount;
    spent += p.totalSolSpentLamports;
    received += p.totalSolReceivedLamports;
    const avgCost =
      p.totalBoughtRaw > 0 ? p.totalSolSpentLamports / p.totalBoughtRaw : 0;
    pnl += p.totalSolReceivedLamports - avgCost * p.totalSoldRaw;
    if (p.totalBoughtRaw - p.totalSoldRaw > 0) open++;
  }
  return {
    buysCount: buys,
    sellsCount: sells,
    tradesCount: buys + sells,
    totalSolSpentLamports: spent,
    totalSolReceivedLamports: received,
    realizedPnlLamports: pnl,
    openPositions: open,
    trackedTokens: positions.size,
  };
}
