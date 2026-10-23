import "dotenv/config";
import { heliusAddressTransactionsUrl } from "./config.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

type HeliusTx = {
  signature: string;
  timestamp?: number;
  type?: string;
  source?: string;
  feePayer?: string;
  accountData?: Array<{
    account: string;
    nativeBalanceChange?: number;
    tokenBalanceChanges?: Array<{
      userAccount?: string;
      mint?: string;
      rawTokenAmount?: { tokenAmount?: string; decimals?: number };
    }>;
  }>;
  tokenTransfers?: Array<{
    mint?: string;
    fromUserAccount?: string;
    toUserAccount?: string;
    tokenAmount?: number;
    rawTokenAmount?: { tokenAmount?: string; decimals?: number };
  }>;
  nativeTransfers?: Array<{
    fromUserAccount?: string;
    toUserAccount?: string;
    amount?: number;
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

type EarlyTrade = Swap & {
  order: number;
  secondsAfterFirst: number;
};

type MintProfile = {
  mint: string;
  sourceBuy: Swap;
  sourceSell?: Swap;
  pnlPct: number | null;
  creator: string | null;
  firstSeenAt: number;
  sourceDelaySeconds: number | null;
  sourceTradeOrder: number | null;
  sourceBuySol: number;
  earlierBuys: number;
  earlierBuySol: number;
  topEarlierBuyer: string | null;
  topEarlierBuyerSol: number;
};

function short(addr: string | null | undefined): string {
  if (!addr) return "-";
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function sol(lamports: number): number {
  return lamports / 1_000_000_000;
}

function fmtSol(lamports: number): string {
  return `${sol(lamports).toFixed(4)} SOL`;
}

function fmtPct(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, number>();

  for (const account of tx.accountData ?? []) {
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

function extractTokenWallets(tx: HeliusTx, mint: string): string[] {
  const wallets = new Set<string>();
  for (const transfer of tx.tokenTransfers ?? []) {
    if (transfer.mint !== mint) continue;
    if (transfer.fromUserAccount) wallets.add(transfer.fromUserAccount);
    if (transfer.toUserAccount) wallets.add(transfer.toUserAccount);
  }
  for (const account of tx.accountData ?? []) {
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.mint === mint && change.userAccount) wallets.add(change.userAccount);
    }
  }
  return [...wallets];
}

function extractWalletSwapForMint(tx: HeliusTx, mint: string, wallet: string): Swap | null {
  const swap = extractSwap(tx, wallet);
  return swap?.mint === mint ? swap : null;
}

function inferCreator(txs: HeliusTx[], mint: string): string | null {
  const createTx =
    txs.find((tx) => tx.type === "CREATE" || tx.type === "CREATE_POOL") ??
    txs.find((tx) => extractTokenWallets(tx, mint).length > 0);
  return createTx?.feePayer ?? null;
}

function pnlForMint(swaps: Swap[], mint: string): { firstBuy?: Swap; firstSell?: Swap; pnlPct: number | null } {
  const tokenSwaps = swaps.filter((swap) => swap.mint === mint);
  const firstBuy = tokenSwaps.find((swap) => swap.side === "buy");
  const firstSell = tokenSwaps.find((swap) => swap.side === "sell");
  if (!firstBuy || !firstSell || firstBuy.tokenRaw <= 0) return { firstBuy, firstSell, pnlPct: null };
  const soldCost = (firstBuy.solLamports / firstBuy.tokenRaw) * Math.min(firstSell.tokenRaw, firstBuy.tokenRaw);
  const pnlPct = soldCost > 0 ? ((firstSell.solLamports - soldCost) / soldCost) * 100 : null;
  return { firstBuy, firstSell, pnlPct };
}

async function fetchAddressTxs(address: string, limit: number, before?: string): Promise<HeliusTx[]> {
  const url = heliusAddressTransactionsUrl(address, before, limit);
  if (!url) throw new Error("HELIUS_API_KEY is not set");
  const response = await fetch(url);
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Helius ${response.status}: ${text.slice(0, 220)}`);
  }
  return (await response.json()) as HeliusTx[];
}

async function fetchPagedAddressTxs(address: string, limit: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < limit) {
    const batch = await fetchAddressTxs(address, Math.min(100, limit - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  return all;
}

async function fetchAddressTxsUntil(address: string, shouldStop: (txs: HeliusTx[]) => boolean, maxTxs: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(address, Math.min(100, maxTxs - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    if (shouldStop(all)) break;
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  return all;
}

function summarizeEarlyTrades(mint: string, txs: HeliusTx[], sourceWallet: string): EarlyTrade[] {
  const rows: EarlyTrade[] = [];
  const firstSeenAt = Math.min(...txs.map((tx) => tx.timestamp ?? Number.MAX_SAFE_INTEGER));
  if (!Number.isFinite(firstSeenAt)) return rows;

  for (const tx of txs) {
    const wallets = extractTokenWallets(tx, mint);
    for (const wallet of wallets) {
      const swap = extractWalletSwapForMint(tx, mint, wallet);
      if (!swap || swap.side !== "buy") continue;
      rows.push({
        ...swap,
        wallet: wallet === sourceWallet ? sourceWallet : wallet,
        order: 0,
        secondsAfterFirst: (swap.timestamp - firstSeenAt) || 0,
      });
    }
  }

  rows.sort((a, b) => a.timestamp - b.timestamp || b.solLamports - a.solLamports);
  return rows.map((row, index) => ({ ...row, order: index + 1 }));
}

async function profileMint(mint: string, sourceBuy: Swap, sourceSell: Swap | undefined, pnlPct: number | null, sourceWallet: string): Promise<MintProfile> {
  const txs = (
    await fetchAddressTxsUntil(
      mint,
      (rows) => rows.some((tx) => tx.signature === sourceBuy.signature) || rows.some((tx) => (tx.timestamp ?? 0) < sourceBuy.timestamp - 120),
      500
    )
  ).sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
  const seenBeforeSource = txs.filter((tx) => (tx.timestamp ?? Number.MAX_SAFE_INTEGER) <= sourceBuy.timestamp);
  const rawFirstSeenAt = seenBeforeSource[0]?.timestamp ?? txs[0]?.timestamp ?? sourceBuy.timestamp;
  const firstSeenAt = rawFirstSeenAt > sourceBuy.timestamp + 2 ? sourceBuy.timestamp : rawFirstSeenAt;
  const earlyTrades = summarizeEarlyTrades(mint, txs, sourceWallet);
  const sourceTrade = earlyTrades.find((trade) => trade.signature === sourceBuy.signature || trade.wallet === sourceWallet);
  const earlier = earlyTrades.filter((trade) => trade.timestamp <= sourceBuy.timestamp && trade.wallet !== sourceWallet);
  const earlierByWallet = new Map<string, number>();
  for (const trade of earlier) {
    earlierByWallet.set(trade.wallet, (earlierByWallet.get(trade.wallet) ?? 0) + trade.solLamports);
  }
  const [topEarlierBuyer, topEarlierBuyerSol = 0] =
    [...earlierByWallet.entries()].sort((a, b) => b[1] - a[1])[0] ?? [null, 0];

  return {
    mint,
    sourceBuy,
    sourceSell,
    pnlPct,
    creator: inferCreator(txs, mint),
    firstSeenAt,
    sourceDelaySeconds:
      firstSeenAt > 0 && firstSeenAt <= sourceBuy.timestamp + 2
        ? Math.max(0, sourceBuy.timestamp - firstSeenAt)
        : null,
    sourceTradeOrder: sourceTrade?.order ?? null,
    sourceBuySol: sol(sourceBuy.solLamports),
    earlierBuys: earlier.length,
    earlierBuySol: sol(earlier.reduce((sum, trade) => sum + trade.solLamports, 0)),
    topEarlierBuyer,
    topEarlierBuyerSol: sol(topEarlierBuyerSol),
  };
}

function printProfiles(wallet: string, profiles: MintProfile[]): void {
  const closed = profiles.filter((p) => p.pnlPct != null);
  const wins = closed.filter((p) => (p.pnlPct ?? 0) > 0);
  const avgDelay =
    profiles.reduce((sum, p) => sum + (p.sourceDelaySeconds ?? 0), 0) / Math.max(1, profiles.length);
  const avgOrder =
    profiles.reduce((sum, p) => sum + (p.sourceTradeOrder ?? 0), 0) / Math.max(1, profiles.length);

  console.log(`Source edge profile for ${short(wallet)}`);
  console.log(`  Mints profiled: ${profiles.length}`);
  console.log(`  Closed in sample: ${closed.length}; wins: ${wins.length}/${closed.length}`);
  console.log(`  Avg source delay from first mint tx: ${avgDelay.toFixed(1)}s`);
  console.log(`  Avg source buy order in first mint history: ${avgOrder.toFixed(1)}`);
  console.log("");
  console.log("Recent entries:");

  for (const p of profiles) {
    console.log(
      [
        `  ${short(p.mint)}`,
        `pnl=${fmtPct(p.pnlPct)}`,
        `buy=${p.sourceBuySol.toFixed(3)} SOL`,
        `delay=${p.sourceDelaySeconds == null ? "-" : `${p.sourceDelaySeconds}s`}`,
        `order=${p.sourceTradeOrder ?? "-"}`,
        `earlier=${p.earlierBuys} buys/${p.earlierBuySol.toFixed(3)} SOL`,
        `creator=${short(p.creator)}`,
        `topEarly=${short(p.topEarlierBuyer)}:${p.topEarlierBuyerSol.toFixed(3)} SOL`,
      ].join(" | ")
    );
  }

  const creatorCounts = new Map<string, number>();
  const earlyBuyerCounts = new Map<string, number>();
  for (const p of profiles) {
    if (p.creator) creatorCounts.set(p.creator, (creatorCounts.get(p.creator) ?? 0) + 1);
    if (p.topEarlierBuyer) earlyBuyerCounts.set(p.topEarlierBuyer, (earlyBuyerCounts.get(p.topEarlierBuyer) ?? 0) + 1);
  }
  const repeatedCreators = [...creatorCounts.entries()].filter(([, count]) => count > 1).sort((a, b) => b[1] - a[1]);
  const repeatedEarlyBuyers = [...earlyBuyerCounts.entries()].filter(([, count]) => count > 1).sort((a, b) => b[1] - a[1]);

  console.log("");
  console.log("Repeated creators:");
  for (const [creator, count] of repeatedCreators.slice(0, 8)) console.log(`  ${short(creator)} appeared ${count}x`);
  if (repeatedCreators.length === 0) console.log("  None in this sample.");

  console.log("");
  console.log("Repeated top early buyers:");
  for (const [buyer, count] of repeatedEarlyBuyers.slice(0, 8)) console.log(`  ${short(buyer)} appeared ${count}x`);
  if (repeatedEarlyBuyers.length === 0) console.log("  None in this sample.");

  console.log("");
  console.log("Interpretation:");
  console.log("  Low delay/order means the source wallet itself is an early sniper.");
  console.log("  Repeated creators or early buyers are candidate lead wallets to track before the source buys.");
  console.log("  Strong positive PnL clustered around a creator/early buyer is the edge candidate; random one-offs are noise.");
}

async function main(): Promise<void> {
  const wallet = (process.argv[2] ?? process.env.TRACKED_WALLETS?.split(",")[0] ?? "").trim();
  const walletTxLimit = Number(process.argv[3] ?? "80");
  const mintLimit = Number(process.argv[4] ?? "12");
  if (!wallet) throw new Error("Pass a wallet arg or set TRACKED_WALLETS");

  const walletTxs = await fetchPagedAddressTxs(wallet, walletTxLimit);
  const swaps = walletTxs
    .map((tx) => extractSwap(tx, wallet))
    .filter((swap): swap is Swap => swap != null)
    .sort((a, b) => a.timestamp - b.timestamp);

  const firstBuyByMint = new Map<string, Swap>();
  for (const swap of swaps) {
    if (swap.side === "buy" && !firstBuyByMint.has(swap.mint)) firstBuyByMint.set(swap.mint, swap);
  }

  const profiles: MintProfile[] = [];
  const recentBuys = [...firstBuyByMint.values()].sort((a, b) => b.timestamp - a.timestamp).slice(0, mintLimit);
  for (const buy of recentBuys) {
    const { firstSell, pnlPct } = pnlForMint(swaps, buy.mint);
    try {
      profiles.push(await profileMint(buy.mint, buy, firstSell, pnlPct, wallet));
      await sleep(650);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Skipping ${short(buy.mint)}: ${msg}`);
    }
  }

  printProfiles(wallet, profiles);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
