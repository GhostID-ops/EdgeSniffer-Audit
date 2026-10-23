import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-cluster-map-cache");

type HeliusTx = {
  signature: string;
  timestamp?: number;
  type?: string;
  source?: string;
  feePayer?: string;
  nativeTransfers?: Array<{ fromUserAccount?: string; toUserAccount?: string; amount?: number }>;
  tokenTransfers?: Array<{ mint?: string; fromUserAccount?: string; toUserAccount?: string; tokenAmount?: number }>;
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

type WalletStats = {
  wallet: string;
  buys: number;
  sells: number;
  buySol: number;
  sellSol: number;
  transferIn: number;
  transferOut: number;
  firstBuys: number;
  firstSells: number;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(addr: string): string {
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function sol(lamports: number): number {
  return lamports / 1_000_000_000;
}

function fmtSol(lamports: number): string {
  return `${sol(lamports).toFixed(3)} SOL`;
}

function iso(ts: number): string {
  return new Date(ts * 1000).toISOString();
}

async function cacheGet<T>(key: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(path.join(CACHE_DIR, key), "utf8")) as T;
  } catch {
    return null;
  }
}

async function cacheSet(key: string, value: unknown): Promise<void> {
  await mkdir(CACHE_DIR, { recursive: true });
  await writeFile(path.join(CACHE_DIR, key), JSON.stringify(value), "utf8");
}

async function fetchAddressTxs(address: string, limit: number, before?: string): Promise<HeliusTx[]> {
  const url = heliusAddressTransactionsUrl(address, before, limit);
  if (!url) throw new Error("HELIUS_API_KEY is not set");
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 220)}`);
  return (await response.json()) as HeliusTx[];
}

async function fetchPagedAddressTxs(address: string, maxTxs: number): Promise<HeliusTx[]> {
  const key = `wallet-${address}-${maxTxs}.json`;
  const cached = await cacheGet<HeliusTx[]>(key);
  if (cached) return cached;
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(address, Math.min(100, maxTxs - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(800);
  }
  await cacheSet(key, all);
  return all;
}

function dedupe(txs: HeliusTx[]): HeliusTx[] {
  const map = new Map<string, HeliusTx>();
  for (const tx of txs) map.set(tx.signature, tx);
  return [...map.values()].sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, number>();
  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!mint || mint === WRAPPED_SOL_MINT || !Number.isFinite(raw) || raw === 0) continue;
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
  const quoteLamports =
    tokenRaw > 0 && nativeNet < 0 ? Math.abs(nativeNet) : tokenRaw < 0 && nativeNet > 0 ? nativeNet : 0;
  if (!mint || tokenRaw === 0 || quoteLamports <= 0) return null;
  return {
    wallet,
    signature: tx.signature,
    timestamp: tx.timestamp ?? 0,
    side: tokenRaw > 0 ? "buy" : "sell",
    mint,
    solLamports: quoteLamports,
    tokenRaw: Math.abs(tokenRaw),
  };
}

async function label(mint: string): Promise<string> {
  try {
    const info = await getTokenPairInfo(mint);
    const symbol = info?.baseToken?.symbol;
    return symbol ? `${symbol} (${short(mint)})` : short(mint);
  } catch {
    return short(mint);
  }
}

function statsFor(wallets: string[]): Map<string, WalletStats> {
  return new Map(
    wallets.map((wallet) => [
      wallet,
      { wallet, buys: 0, sells: 0, buySol: 0, sellSol: 0, transferIn: 0, transferOut: 0, firstBuys: 0, firstSells: 0 },
    ])
  );
}

async function main(): Promise<void> {
  const wallets = (process.argv[2] ?? "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const maxTxs = Number(process.argv[3] ?? "800");
  if (wallets.length < 2) throw new Error("usage: tsx src/walletClusterMap.ts WALLET1,WALLET2,... [maxTxs]");

  console.log(`Cluster map wallets=${wallets.map(short).join(", ")} maxTxs=${maxTxs}`);
  const all: HeliusTx[] = [];
  for (const wallet of wallets) {
    console.log(`Fetching ${short(wallet)}`);
    all.push(...(await fetchPagedAddressTxs(wallet, maxTxs)));
    await sleep(1200);
  }

  const walletSet = new Set(wallets);
  const txs = dedupe(all);
  const swaps = txs.flatMap((tx) => wallets.map((wallet) => extractSwap(tx, wallet))).filter((x): x is Swap => x != null);
  const moves = txs.flatMap((tx) =>
    (tx.tokenTransfers ?? [])
      .filter((t) => t.mint && t.fromUserAccount && t.toUserAccount && walletSet.has(t.fromUserAccount) && walletSet.has(t.toUserAccount))
      .map((t) => ({
        timestamp: tx.timestamp ?? 0,
        signature: tx.signature,
        mint: t.mint!,
        from: t.fromUserAccount!,
        to: t.toUserAccount!,
        amount: t.tokenAmount ?? null,
      }))
  );

  const stats = statsFor(wallets);
  for (const swap of swaps) {
    const row = stats.get(swap.wallet)!;
    if (swap.side === "buy") {
      row.buys++;
      row.buySol += swap.solLamports;
    } else {
      row.sells++;
      row.sellSol += swap.solLamports;
    }
  }
  for (const move of moves) {
    stats.get(move.from)!.transferOut++;
    stats.get(move.to)!.transferIn++;
  }

  const swapsByMint = new Map<string, Swap[]>();
  for (const swap of swaps) {
    const rows = swapsByMint.get(swap.mint) ?? [];
    rows.push(swap);
    swapsByMint.set(swap.mint, rows);
  }
  for (const rows of swapsByMint.values()) {
    const sorted = rows.sort((a, b) => a.timestamp - b.timestamp);
    const firstBuy = sorted.find((row) => row.side === "buy");
    const firstSell = sorted.find((row) => row.side === "sell");
    if (firstBuy) stats.get(firstBuy.wallet)!.firstBuys++;
    if (firstSell) stats.get(firstSell.wallet)!.firstSells++;
  }

  console.log("");
  console.log("Wallet Roles");
  for (const row of [...stats.values()].sort((a, b) => b.buySol - a.buySol)) {
    console.log(
      `${short(row.wallet)} buys=${row.buys} buySol=${fmtSol(row.buySol)} sells=${row.sells} sellSol=${fmtSol(row.sellSol)} ` +
        `xferOut=${row.transferOut} xferIn=${row.transferIn} firstBuys=${row.firstBuys} firstSells=${row.firstSells}`
    );
  }

  const edgeRows = new Map<string, number>();
  for (const move of moves) {
    const key = `${short(move.from)} -> ${short(move.to)}`;
    edgeRows.set(key, (edgeRows.get(key) ?? 0) + 1);
  }
  console.log("");
  console.log("Internal Token Transfer Edges");
  for (const [key, count] of [...edgeRows.entries()].sort((a, b) => b[1] - a[1])) {
    console.log(`  ${key}: ${count}`);
  }

  console.log("");
  console.log("Recent Internal Transfers");
  for (const move of moves.sort((a, b) => b.timestamp - a.timestamp).slice(0, 40)) {
    console.log(`${iso(move.timestamp)} | ${await label(move.mint)} | ${short(move.from)} -> ${short(move.to)} | amount=${move.amount ?? "-"} | sig=${move.signature.slice(0, 10)}`);
  }

  console.log("");
  console.log("Recent Mint Timelines");
  for (const mint of [...new Set(moves.sort((a, b) => b.timestamp - a.timestamp).map((m) => m.mint))].slice(0, 15)) {
    const mintSwaps = (swapsByMint.get(mint) ?? []).sort((a, b) => a.timestamp - b.timestamp);
    const mintMoves = moves.filter((m) => m.mint === mint).sort((a, b) => a.timestamp - b.timestamp);
    console.log("");
    console.log(`  ${await label(mint)}`);
    for (const swap of mintSwaps.slice(0, 12)) {
      console.log(`    ${iso(swap.timestamp)} ${short(swap.wallet)} ${swap.side} ${fmtSol(swap.solLamports)} sig=${swap.signature.slice(0, 10)}`);
    }
    for (const move of mintMoves.slice(0, 8)) {
      console.log(`    ${iso(move.timestamp)} transfer ${short(move.from)} -> ${short(move.to)} amount=${move.amount ?? "-"} sig=${move.signature.slice(0, 10)}`);
    }
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
