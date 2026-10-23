import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

// Who enters first? Ranks a set of wallets by how often each is the first to BUY
// a token that two or more of them share. Block-level ordering (slot) is the
// primary key because these snipes routinely land in the same wall-clock second.
//
// usage: tsx src/walletLeadOrder.ts [maxTxs] [WALLET ...]
// With no wallet args it uses the default cluster below.

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-cluster-cache");

const DEFAULT_WALLETS = [
  "GUgMNi2tJcjYpDSFs2VZQpjryeWRG4Xgmi1JPDEoe1TH",
  "5ATd36Tdq6zrtHBokC5moRs9Y5EFSNaZQ12fzRmTeNWd",
  "5qnMBQUtBYy9PFQah7fCn59k95xMkZfVUahJdtK3t3MF",
  "EAzB32pcd8xP8hQgaAVH9FNKtGW34EuuowfPpprZAGF5",
];

type HeliusTx = {
  signature: string;
  timestamp?: number;
  slot?: number;
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

type Buy = {
  wallet: string;
  signature: string;
  timestamp: number;
  slot: number;
  mint: string;
  solLamports: number;
};

function short(addr: string): string {
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function sol(lamports: number): number {
  return lamports / 1_000_000_000;
}

function fmtAge(ts: number): string {
  return ts > 0 ? new Date(ts * 1000).toISOString() : "-";
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!;
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

// Shares the same cache key scheme as walletClusterTrace.ts so pulls are reused.
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
    await sleep(650);
  }
  await cacheSet(key, all);
  return all;
}

// Net token/native deltas for `wallet`; a positive non-WSOL token delta funded
// by negative SOL/WSOL is a buy. Mirrors extractSwap in walletClusterTrace.ts.
function extractBuy(tx: HeliusTx, wallet: string): Buy | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, number>();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!mint || !Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
    }
  }

  let mint: string | null = null;
  let tokenRaw = 0;
  for (const [candidate, net] of tokenNet) {
    if (candidate === WRAPPED_SOL_MINT) continue;
    if (Math.abs(net) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = net;
    }
  }
  if (!mint || tokenRaw <= 0) return null; // only buys

  const wsolNet = tokenNet.get(WRAPPED_SOL_MINT) ?? 0;
  const quoteLamports = wsolNet < 0 ? Math.abs(wsolNet) : nativeNet < 0 ? Math.abs(nativeNet) : 0;
  if (quoteLamports <= 0) return null;

  return {
    wallet,
    signature: tx.signature,
    timestamp: tx.timestamp ?? 0,
    slot: tx.slot ?? 0,
    mint,
    solLamports: quoteLamports,
  };
}

async function labelMint(mint: string): Promise<string> {
  try {
    const info = await getTokenPairInfo(mint);
    const symbol = info?.baseToken?.symbol;
    return symbol ? `${symbol} (${short(mint)})` : short(mint);
  } catch {
    return short(mint);
  }
}

// First buy each wallet made on a mint (earliest slot, then timestamp).
function firstBuyPerWallet(buys: Buy[]): Map<string, Buy> {
  const out = new Map<string, Buy>();
  for (const buy of buys) {
    const prev = out.get(buy.wallet);
    if (!prev || buy.slot < prev.slot || (buy.slot === prev.slot && buy.timestamp < prev.timestamp)) {
      out.set(buy.wallet, buy);
    }
  }
  return out;
}

type WalletStat = {
  wallet: string;
  totalBuys: number;
  shared: number; // shared mints this wallet participated in
  soleFirst: number; // strictly first (own slot)
  tiedFirst: number; // tied for first (same slot as another)
  rankSum: number; // for average finishing rank on shared mints
};

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const maxTxs = Number.isFinite(Number(args[0])) && args[0] ? Number(args[0]) : 2500;
  const wallets = (Number.isFinite(Number(args[0])) ? args.slice(1) : args).filter(Boolean);
  const targets = (wallets.length > 0 ? wallets : DEFAULT_WALLETS).map((w) => w.trim());

  console.log(`Lead-order analysis — ${targets.length} wallets, maxTxs=${maxTxs} each`);
  for (const w of targets) console.log(`  ${short(w)}`);

  const allBuys: Buy[] = [];
  const totalBuysByWallet = new Map<string, number>();
  for (const wallet of targets) {
    const txs = await fetchPagedAddressTxs(wallet, maxTxs);
    const buys = txs.map((tx) => extractBuy(tx, wallet)).filter((b): b is Buy => b != null);
    totalBuysByWallet.set(wallet, buys.length);
    allBuys.push(...buys);
    console.log(`  fetched ${txs.length} txs / ${buys.length} buys for ${short(wallet)}`);
  }

  // Group buys by mint.
  const byMint = new Map<string, Buy[]>();
  for (const buy of allBuys) {
    const rows = byMint.get(buy.mint) ?? [];
    rows.push(buy);
    byMint.set(buy.mint, rows);
  }

  const stats = new Map<string, WalletStat>();
  for (const wallet of targets) {
    stats.set(wallet, {
      wallet,
      totalBuys: totalBuysByWallet.get(wallet) ?? 0,
      shared: 0,
      soleFirst: 0,
      tiedFirst: 0,
      rankSum: 0,
    });
  }

  type Contest = {
    mint: string;
    ordered: Buy[];
    winners: Buy[]; // could be >1 on a same-slot tie
    slotGapToNext: number | null; // winner slot vs next distinct slot
    secGapToNext: number | null;
  };
  const contests: Contest[] = [];
  const slotGaps: number[] = [];
  const secGaps: number[] = [];

  for (const [mint, buys] of byMint) {
    const firsts = [...firstBuyPerWallet(buys).values()];
    if (firsts.length < 2) continue; // need ≥2 of the target wallets in the same token

    const ordered = firsts.sort((a, b) => a.slot - b.slot || a.timestamp - b.timestamp);
    const winnerSlot = ordered[0]!.slot;
    const winners = ordered.filter((b) => b.slot === winnerSlot);
    const next = ordered.find((b) => b.slot > winnerSlot) ?? null;
    const slotGapToNext = next ? next.slot - winnerSlot : null;
    const secGapToNext = next ? next.timestamp - ordered[0]!.timestamp : null;
    if (slotGapToNext != null) slotGaps.push(slotGapToNext);
    if (secGapToNext != null) secGaps.push(secGapToNext);

    // Rank: same slot shares the same (dense) rank.
    let rank = 0;
    let lastSlot = -1;
    for (const buy of ordered) {
      if (buy.slot !== lastSlot) {
        rank += 1;
        lastSlot = buy.slot;
      }
      const s = stats.get(buy.wallet)!;
      s.shared += 1;
      s.rankSum += rank;
    }
    for (const w of winners) {
      const s = stats.get(w.wallet)!;
      if (winners.length === 1) s.soleFirst += 1;
      else s.tiedFirst += 1;
    }

    contests.push({ mint, ordered, winners, slotGapToNext, secGapToNext });
  }

  const totalContested = contests.length;

  console.log("");
  console.log(`Shared tokens (bought by ≥2 of the wallets): ${totalContested}`);
  console.log("");
  console.log("Lead leaderboard");
  console.log("  wallet            buys  shared  1st(sole)  1st(tie)  leadRate  avgRank");

  const ranked = [...stats.values()].sort((a, b) => {
    const ar = a.shared ? (a.soleFirst + a.tiedFirst) / a.shared : 0;
    const br = b.shared ? (b.soleFirst + b.tiedFirst) / b.shared : 0;
    return br - ar;
  });
  for (const s of ranked) {
    const leadRate = s.shared ? ((s.soleFirst + s.tiedFirst) / s.shared) * 100 : 0;
    const avgRank = s.shared ? s.rankSum / s.shared : 0;
    console.log(
      `  ${short(s.wallet).padEnd(15)}  ${String(s.totalBuys).padStart(4)}  ${String(s.shared).padStart(6)}  ` +
        `${String(s.soleFirst).padStart(9)}  ${String(s.tiedFirst).padStart(8)}  ` +
        `${leadRate.toFixed(1).padStart(7)}%  ${avgRank.toFixed(2).padStart(7)}`
    );
  }

  const medSlot = median(slotGaps);
  const medSec = median(secGaps);
  console.log("");
  console.log("Head start of the first mover over the 2nd wallet (on contested tokens)");
  console.log(`  median slot gap: ${medSlot ?? "-"} (~${medSlot != null ? (medSlot * 0.4).toFixed(1) : "-"}s at ~400ms/slot)`);
  console.log(`  median timestamp gap: ${medSec ?? "-"}s`);

  // Sample of the most recent contested tokens with full ordering.
  const recent = contests.sort((a, b) => b.ordered[0]!.timestamp - a.ordered[0]!.timestamp).slice(0, 20);
  console.log("");
  console.log("Recent contested tokens (entry order)");
  for (const contest of recent) {
    console.log("");
    console.log(`  ${await labelMint(contest.mint)}  (slotGap→2nd: ${contest.slotGapToNext ?? "-"})`);
    let rank = 0;
    let lastSlot = -1;
    for (const buy of contest.ordered) {
      if (buy.slot !== lastSlot) {
        rank += 1;
        lastSlot = buy.slot;
      }
      console.log(
        `    #${rank} ${short(buy.wallet)} slot=${buy.slot} ${fmtAge(buy.timestamp)} ${sol(buy.solLamports).toFixed(4)} SOL`
      );
    }
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
