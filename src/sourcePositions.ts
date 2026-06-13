export type SourcePosition = {
  wallet: string;
  mint: string;
  totalBoughtRaw: number;
  totalSoldRaw: number;
  buysCount: number;
  sellsCount: number;
  firstSeenAt: number;
  lastTradeAt: number;
};

const sourcePositions = new Map<string, SourcePosition>();

function key(wallet: string, mint: string): string {
  return `${wallet}:${mint}`;
}

function ensure(wallet: string, mint: string): SourcePosition {
  const k = key(wallet, mint);
  let p = sourcePositions.get(k);
  if (!p) {
    const now = Date.now();
    p = {
      wallet,
      mint,
      totalBoughtRaw: 0,
      totalSoldRaw: 0,
      buysCount: 0,
      sellsCount: 0,
      firstSeenAt: now,
      lastTradeAt: now,
    };
    sourcePositions.set(k, p);
  }
  return p;
}

export function recordSourceBuyRaw(wallet: string, mint: string, tokenAmountRaw: number): SourcePosition | null {
  if (!Number.isFinite(tokenAmountRaw) || tokenAmountRaw <= 0) return null;
  const p = ensure(wallet, mint);
  p.totalBoughtRaw += Math.floor(tokenAmountRaw);
  p.buysCount++;
  p.lastTradeAt = Date.now();
  return p;
}

export function recordSourceSellRaw(wallet: string, mint: string, tokenAmountRaw: number): SourcePosition | null {
  if (!Number.isFinite(tokenAmountRaw) || tokenAmountRaw <= 0) return null;
  const p = ensure(wallet, mint);
  p.totalSoldRaw += Math.floor(tokenAmountRaw);
  p.sellsCount++;
  p.lastTradeAt = Date.now();
  return p;
}

export function getSourcePosition(wallet: string, mint: string): SourcePosition | null {
  return sourcePositions.get(key(wallet, mint)) ?? null;
}

export function getSourceHeldRaw(wallet: string, mint: string): number {
  const p = getSourcePosition(wallet, mint);
  if (!p) return 0;
  return Math.max(p.totalBoughtRaw - p.totalSoldRaw, 0);
}

export function calculateProportionalSellRaw(args: {
  wallet: string;
  mint: string;
  sourceSellAmountRaw: number;
  ourHeldRaw: number;
  minPct?: number;
  maxPct?: number;
}): { amountRaw: number; fractionPct: number; sourceHeldRaw: number; fallback: boolean } {
  const sourceHeldRaw = getSourceHeldRaw(args.wallet, args.mint);
  if (args.ourHeldRaw <= 0 || args.sourceSellAmountRaw <= 0 || sourceHeldRaw <= 0) {
    return { amountRaw: 0, fractionPct: 0, sourceHeldRaw, fallback: true };
  }

  const rawFractionPct = (args.sourceSellAmountRaw / sourceHeldRaw) * 100;
  const minPct = Math.max(0, args.minPct ?? 0);
  const maxPct = Math.max(minPct, Math.min(100, args.maxPct ?? 100));
  const fractionPct = Math.max(minPct, Math.min(maxPct, rawFractionPct));
  const amountRaw = Math.min(args.ourHeldRaw, Math.max(1, Math.floor((args.ourHeldRaw * fractionPct) / 100)));

  return { amountRaw, fractionPct, sourceHeldRaw, fallback: false };
}

export function clearSourcePositions(): void {
  sourcePositions.clear();
}
