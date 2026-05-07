export interface SnipePosition {
  mint: string;
  spentLamports: number;
  receivedRawTokens: number;
  buySignature: string;
  boughtAt: number;
  closed: boolean;
  simulated: boolean;
  exitSignature?: string;
  receivedLamportsOnExit?: number;
  closedAt?: number;
}

const positions = new Map<string, SnipePosition>();

export function recordSnipeBuy(
  mint: string,
  spentLamports: number,
  receivedRawTokens: number,
  buySignature: string,
  simulated: boolean = false
): SnipePosition {
  const pos: SnipePosition = {
    mint,
    spentLamports,
    receivedRawTokens,
    buySignature,
    boughtAt: Date.now(),
    closed: false,
    simulated,
  };
  positions.set(mint, pos);
  return pos;
}

export function getAllSnipes(): SnipePosition[] {
  return Array.from(positions.values());
}

export interface SessionPnl {
  closedCount: number;
  openCount: number;
  totalSpentLamports: number;
  totalReceivedLamports: number;
  realizedPnlLamports: number;
  wins: number;
  losses: number;
}

export function getSessionPnl(): SessionPnl {
  let closed = 0;
  let open = 0;
  let spent = 0;
  let received = 0;
  let pnl = 0;
  let wins = 0;
  let losses = 0;
  for (const p of positions.values()) {
    if (p.closed) {
      closed++;
      spent += p.spentLamports;
      received += p.receivedLamportsOnExit ?? 0;
      const delta = (p.receivedLamportsOnExit ?? 0) - p.spentLamports;
      pnl += delta;
      if (delta > 0) wins++;
      else if (delta < 0) losses++;
    } else {
      open++;
    }
  }
  return {
    closedCount: closed,
    openCount: open,
    totalSpentLamports: spent,
    totalReceivedLamports: received,
    realizedPnlLamports: pnl,
    wins,
    losses,
  };
}

export function recordSnipeExit(
  mint: string,
  exitSignature: string,
  receivedLamports: number
): SnipePosition | null {
  const pos = positions.get(mint);
  if (!pos) return null;
  pos.closed = true;
  pos.exitSignature = exitSignature;
  pos.receivedLamportsOnExit = receivedLamports;
  pos.closedAt = Date.now();
  return pos;
}

export function getOpenSnipes(): SnipePosition[] {
  return Array.from(positions.values()).filter((p) => !p.closed);
}

export function getSnipe(mint: string): SnipePosition | undefined {
  return positions.get(mint);
}

export function findSnipeByMintPrefix(prefix: string): SnipePosition | undefined {
  if (!prefix) return undefined;
  for (const pos of positions.values()) {
    if (pos.mint === prefix) return pos;
  }
  for (const pos of positions.values()) {
    if (!pos.closed && pos.mint.toLowerCase().startsWith(prefix.toLowerCase())) {
      return pos;
    }
  }
  return undefined;
}
