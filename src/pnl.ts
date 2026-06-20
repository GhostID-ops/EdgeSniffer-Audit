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
  realizedCostBasisLamports: number;
  realizedProceedsLamports: number;
  realizedPnlLamports: number;
  uncostedProceedsLamports: number;
  costBasisRemainingLamports: number;
  soldRawCosted: number;
  soldRawUncosted: number;
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
  const soldRawCosted = Math.min(p.totalSoldRaw, p.totalBoughtRaw);
  const soldRawUncosted = Math.max(0, p.totalSoldRaw - p.totalBoughtRaw);
  const soldCostBasis = avgCost != null ? avgCost * soldRawCosted : 0;
  const realizedProceeds =
    p.totalSoldRaw > 0
      ? p.totalSolReceivedLamports * (soldRawCosted / p.totalSoldRaw)
      : 0;
  const uncostedProceeds = p.totalSolReceivedLamports - realizedProceeds;
  const realizedPnl = realizedProceeds - soldCostBasis;
  const held = p.totalBoughtRaw - p.totalSoldRaw;
  const remainingCost = avgCost != null ? avgCost * Math.max(held, 0) : 0;
  return {
    ...p,
    heldRaw: held,
    avgCostLamportsPerTokenRaw: avgCost,
    realizedCostBasisLamports: soldCostBasis,
    realizedProceedsLamports: realizedProceeds,
    realizedPnlLamports: realizedPnl,
    uncostedProceedsLamports: uncostedProceeds,
    costBasisRemainingLamports: remainingCost,
    soldRawCosted,
    soldRawUncosted,
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

/** Clear all tracked positions. Used by the "reset" keyboard shortcut. */
export function resetPositions(): void {
  positions.clear();
}

/**
 * Roll back a previously recorded buy — used when a tx broadcast successfully but failed on chain
 * (e.g., during background confirmation). Subtracts tokens and SOL from the position. If this was
 * the only buy and there are no sells, the position is fully removed.
 *
 * Returns true if a position was rolled back, false if no matching position existed.
 */
export function unrecordBuy(
  mint: string,
  tokenAmountRaw: number,
  solLamports: number
): boolean {
  const p = positions.get(mint);
  if (!p) return false;
  p.totalBoughtRaw = Math.max(0, p.totalBoughtRaw - tokenAmountRaw);
  p.totalSolSpentLamports = Math.max(0, p.totalSolSpentLamports - solLamports);
  p.buysCount = Math.max(0, p.buysCount - 1);
  // If the position has no buys and no sells left, drop it entirely.
  if (p.buysCount === 0 && p.sellsCount === 0) {
    positions.delete(mint);
  }
  return true;
}

/**
 * Correct the token amount for an already-recorded buy without changing SOL cost. This is used
 * after fast-mode confirmation when Token-2022 transfer-fee mints deliver fewer tokens than the
 * curve quote predicted.
 */
export function adjustBuyTokenAmount(
  mint: string,
  expectedTokenAmountRaw: number,
  actualTokenAmountRaw: number
): boolean {
  const p = positions.get(mint);
  if (!p) return false;
  const delta = actualTokenAmountRaw - expectedTokenAmountRaw;
  if (!Number.isFinite(delta) || delta === 0) return false;
  p.totalBoughtRaw = Math.max(0, p.totalBoughtRaw + delta);
  p.lastTradeAt = Date.now();
  if (p.buysCount === 0 && p.sellsCount === 0) {
    positions.delete(mint);
  }
  return true;
}

/**
 * Correct the SOL cost for an already-recorded buy after confirmation. Fast-mode initially records
 * the swap input only; this lets us include the actual wallet debit, including tx fee/tip.
 */
export function adjustBuySolCost(
  mint: string,
  expectedSolLamports: number,
  actualSolLamports: number
): boolean {
  const p = positions.get(mint);
  if (!p) return false;
  const delta = actualSolLamports - expectedSolLamports;
  if (!Number.isFinite(delta) || delta === 0) return false;
  p.totalSolSpentLamports = Math.max(0, p.totalSolSpentLamports + delta);
  p.lastTradeAt = Date.now();
  return true;
}

/**
 * Remove a local zero-proceeds close that was created while a fast-mode optimistic buy was still
 * unconfirmed. This prevents a later failed-buy rollback from leaving negative token holdings.
 */
export function unrecordZeroSell(
  mint: string,
  tokenAmountRaw: number
): boolean {
  const p = positions.get(mint);
  if (!p) return false;
  p.totalSoldRaw = Math.max(0, p.totalSoldRaw - tokenAmountRaw);
  p.sellsCount = Math.max(0, p.sellsCount - 1);
  p.lastTradeAt = Date.now();
  if (p.buysCount === 0 && p.sellsCount === 0) {
    positions.delete(mint);
  }
  return true;
}

/**
 * Roll back a previously recorded sell when a fast-mode sell broadcast was accepted but later
 * finalized failed on chain. Keeps the local position ledger aligned with wallet reality.
 */
export function unrecordSell(
  mint: string,
  tokenAmountRaw: number,
  solLamports: number
): boolean {
  const p = positions.get(mint);
  if (!p) return false;
  p.totalSoldRaw = Math.max(0, p.totalSoldRaw - tokenAmountRaw);
  p.totalSolReceivedLamports = Math.max(0, p.totalSolReceivedLamports - solLamports);
  p.sellsCount = Math.max(0, p.sellsCount - 1);
  p.lastTradeAt = Date.now();
  if (p.buysCount === 0 && p.sellsCount === 0) {
    positions.delete(mint);
  }
  return true;
}

/**
 * Correct an already-recorded sell after fast-mode confirmation. The optimistic path records the
 * route/curve quote immediately after broadcast; confirmation lets us replace it with actual wallet
 * balance deltas from the landed transaction.
 */
export function adjustSellExecution(
  mint: string,
  expectedTokenAmountRaw: number,
  actualTokenAmountRaw: number,
  expectedSolLamports: number,
  actualSolLamports: number
): boolean {
  const p = positions.get(mint);
  if (!p) return false;
  let changed = false;

  const tokenDelta = actualTokenAmountRaw - expectedTokenAmountRaw;
  if (Number.isFinite(tokenDelta) && tokenDelta !== 0) {
    p.totalSoldRaw = Math.max(0, p.totalSoldRaw + tokenDelta);
    changed = true;
  }

  const solDelta = actualSolLamports - expectedSolLamports;
  if (Number.isFinite(solDelta) && solDelta !== 0) {
    p.totalSolReceivedLamports = Math.max(0, p.totalSolReceivedLamports + solDelta);
    changed = true;
  }

  if (changed) p.lastTradeAt = Date.now();
  if (p.buysCount === 0 && p.sellsCount === 0) {
    positions.delete(mint);
  }
  return changed;
}

export type PnlTotals = {
  buysCount: number;
  sellsCount: number;
  tradesCount: number;
  totalSolSpentLamports: number;
  totalSolReceivedLamports: number;
  realizedCostBasisLamports: number;
  realizedProceedsLamports: number;
  realizedPnlLamports: number;
  uncostedProceedsLamports: number;
  openCostBasisLamports: number;
  openPositions: number;
  trackedTokens: number;
};

export function getTotals(): PnlTotals {
  let buys = 0;
  let sells = 0;
  let spent = 0;
  let received = 0;
  let realizedCostBasis = 0;
  let realizedProceeds = 0;
  let uncostedProceeds = 0;
  let pnl = 0;
  let openCostBasis = 0;
  let open = 0;
  for (const p of positions.values()) {
    buys += p.buysCount;
    sells += p.sellsCount;
    spent += p.totalSolSpentLamports;
    received += p.totalSolReceivedLamports;
    const avgCost =
      p.totalBoughtRaw > 0 ? p.totalSolSpentLamports / p.totalBoughtRaw : 0;
    const heldRaw = p.totalBoughtRaw - p.totalSoldRaw;
    const soldRawCosted = Math.min(p.totalSoldRaw, p.totalBoughtRaw);
    const soldCostBasis = avgCost * soldRawCosted;
    const soldProceedsCosted =
      p.totalSoldRaw > 0
        ? p.totalSolReceivedLamports * (soldRawCosted / p.totalSoldRaw)
        : 0;
    realizedCostBasis += soldCostBasis;
    realizedProceeds += soldProceedsCosted;
    uncostedProceeds += p.totalSolReceivedLamports - soldProceedsCosted;
    pnl += soldProceedsCosted - soldCostBasis;
    if (heldRaw > 0) {
      open++;
      openCostBasis += avgCost * heldRaw;
    }
  }
  return {
    buysCount: buys,
    sellsCount: sells,
    tradesCount: buys + sells,
    totalSolSpentLamports: spent,
    totalSolReceivedLamports: received,
    realizedCostBasisLamports: realizedCostBasis,
    realizedProceedsLamports: realizedProceeds,
    realizedPnlLamports: pnl,
    uncostedProceedsLamports: uncostedProceeds,
    openCostBasisLamports: openCostBasis,
    openPositions: open,
    trackedTokens: positions.size,
  };
}
