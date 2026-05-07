import "dotenv/config";
import { heliusAddressTransactionsUrl } from "./config.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

type HeliusTx = {
  signature: string;
  timestamp?: number;
  type?: string;
  source?: string;
  accountData?: Array<{
    account: string;
    nativeBalanceChange?: number;
    tokenBalanceChanges?: Array<{
      userAccount?: string;
      mint?: string;
      rawTokenAmount?: { tokenAmount?: string; decimals?: number };
    }>;
  }>;
};

type Swap = {
  wallet: string;
  signature: string;
  timestamp: number;
  side: "buy" | "sell";
  mint: string;
  solLamports: number;
  tokenRaw: number;
};

type Position = {
  mint: string;
  boughtRaw: number;
  spentLamports: number;
  soldRaw: number;
  receivedLamports: number;
  buys: number;
  sells: number;
};

function short(addr: string): string {
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function sol(lamports: number): number {
  return lamports / 1_000_000_000;
}

function fmtSol(lamports: number): string {
  return `${sol(lamports).toFixed(4)} SOL`;
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  if (!tx.accountData?.length) return null;

  let nativeNet = 0;
  const tokenNet = new Map<string, number>();

  for (const account of tx.accountData) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      if (!mint || mint === WRAPPED_SOL_MINT) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
    }
  }

  let mint: string | null = null;
  let tokenRaw = 0;
  for (const [candidate, raw] of tokenNet) {
    if (Math.abs(raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = raw;
    }
  }

  if (!mint || tokenRaw === 0 || nativeNet === 0) return null;
  if (nativeNet < 0 && tokenRaw > 0) {
    return {
      wallet,
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "buy",
      mint,
      solLamports: Math.abs(nativeNet),
      tokenRaw: Math.abs(tokenRaw),
    };
  }
  if (nativeNet > 0 && tokenRaw < 0) {
    return {
      wallet,
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "sell",
      mint,
      solLamports: nativeNet,
      tokenRaw: Math.abs(tokenRaw),
    };
  }
  return null;
}

async function fetchWalletTxs(wallet: string, limit: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;

  while (all.length < limit) {
    const batchLimit = Math.min(100, limit - all.length);
    const url = heliusAddressTransactionsUrl(wallet, before, batchLimit);
    if (!url) throw new Error("HELIUS_API_KEY is not set");
    const response = await fetch(url);
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Helius ${response.status}: ${text.slice(0, 160)}`);
    }
    const batch = (await response.json()) as HeliusTx[];
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await new Promise((resolve) => setTimeout(resolve, 650));
  }

  return all;
}

function analyze(wallet: string, swaps: Swap[]): string[] {
  const positions = new Map<string, Position>();
  for (const swap of swaps) {
    const p =
      positions.get(swap.mint) ??
      {
        mint: swap.mint,
        boughtRaw: 0,
        spentLamports: 0,
        soldRaw: 0,
        receivedLamports: 0,
        buys: 0,
        sells: 0,
      };
    if (swap.side === "buy") {
      p.boughtRaw += swap.tokenRaw;
      p.spentLamports += swap.solLamports;
      p.buys++;
    } else {
      p.soldRaw += swap.tokenRaw;
      p.receivedLamports += swap.solLamports;
      p.sells++;
    }
    positions.set(swap.mint, p);
  }

  let realized = 0;
  let spentClosed = 0;
  let wins = 0;
  let losses = 0;
  const closedRows: Array<{ mint: string; pnl: number; roi: number; spent: number; received: number }> = [];

  for (const p of positions.values()) {
    if (p.boughtRaw <= 0 || p.soldRaw <= 0) continue;
    const avgCost = p.spentLamports / p.boughtRaw;
    const costBasisSold = avgCost * Math.min(p.soldRaw, p.boughtRaw);
    const pnl = p.receivedLamports - costBasisSold;
    const roi = costBasisSold > 0 ? (pnl / costBasisSold) * 100 : 0;
    realized += pnl;
    spentClosed += costBasisSold;
    if (pnl > 0) wins++;
    if (pnl < 0) losses++;
    closedRows.push({ mint: p.mint, pnl, roi, spent: costBasisSold, received: p.receivedLamports });
  }

  closedRows.sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl));
  const walletSwaps = swaps.filter((s) => s.wallet === wallet);
  const buys = walletSwaps.filter((s) => s.side === "buy").length;
  const sells = walletSwaps.filter((s) => s.side === "sell").length;
  const roi = spentClosed > 0 ? (realized / spentClosed) * 100 : 0;
  const winRate = wins + losses > 0 ? (wins / (wins + losses)) * 100 : 0;

  const lines = [
    `Wallet ${short(wallet)}`,
    `  Swaps analyzed: ${walletSwaps.length} (${buys} buys, ${sells} sells)`,
    `  Closed tokens: ${closedRows.length}`,
    `  Realized PnL: ${fmtSol(realized)} (${roi.toFixed(1)}% ROI on closed cost basis)`,
    `  Win rate: ${winRate.toFixed(1)}% (${wins}W/${losses}L)`,
    `  Largest closed outcomes:`,
  ];

  for (const row of closedRows.slice(0, 6)) {
    lines.push(
      `    ${short(row.mint)} pnl=${fmtSol(row.pnl)} roi=${row.roi.toFixed(1)}% spent=${fmtSol(row.spent)} received=${fmtSol(row.received)}`
    );
  }

  if (closedRows.length === 0) lines.push("    No closed positions in this sample.");
  return lines;
}

async function main(): Promise<void> {
  const walletsArg = process.argv[2] ?? process.env.TRACKED_WALLETS ?? "";
  const wallets = walletsArg
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const limit = Number(process.argv[3] ?? "80");
  if (wallets.length === 0) throw new Error("Pass wallets as comma-separated arg or set TRACKED_WALLETS");

  for (const wallet of wallets) {
    const txs = await fetchWalletTxs(wallet, limit);
    const swaps = txs
      .map((tx) => extractSwap(tx, wallet))
      .filter((swap): swap is Swap => swap != null)
      .sort((a, b) => a.timestamp - b.timestamp);
    console.log(analyze(wallet, swaps).join("\n"));
    console.log("");
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
