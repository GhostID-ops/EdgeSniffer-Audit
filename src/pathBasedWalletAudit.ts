import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const GECKO_BASE = "https://api.geckoterminal.com/api/v2";
const MORALIS_BASE = "https://solana-gateway.moralis.io";
const MORALIS_API_KEY = process.env.MORALIS_API_KEY ?? "";
const CACHE_DIR = path.resolve(process.cwd(), "data", "path-audit-cache");
const QUIET = (process.env.PATH_AUDIT_QUIET ?? "false").toLowerCase() === "true";

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
  }>;
};

type Swap = {
  signature: string;
  timestamp: number;
  side: "buy" | "sell";
  mint: string;
  solLamports: number;
  tokenRaw: number;
  decimals: number;
};

type Campaign = {
  mint: string;
  firstBuy: Swap;
  firstSell?: Swap;
  buys: Swap[];
  sells: Swap[];
  spentLamports: number;
  boughtRaw: number;
  receivedLamports: number;
  soldRaw: number;
  decimals: number;
};

type Candle = { ts: number; open: number; high: number; low: number; close: number; volume: number };

type PathRow = {
  mint: string;
  symbol: string;
  entryTs: number;
  sourceBuySol: number;
  sourceExitPct: number | null;
  entryMarketCapUsdApprox: number | null;
  pre5mVolumeUsd: number;
  mfePct: number;
  maePct: number;
  timeTo2xSec: number | null;
  candles: Candle[];
  lead: LeadProfile | null;
};

type LeadProfile = {
  creator: string | null;
  sourceDelaySec: number | null;
  sourceOrder: number | null;
  earlierBuys: number;
  earlierBuySol: number;
  topEarlierBuyer: string | null;
  topEarlierBuyerSol: number;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(addr: string | null | undefined): string {
  if (!addr) return "-";
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function sol(lamports: number): number {
  return lamports / 1_000_000_000;
}

function pct(from: number, to: number): number {
  return ((to - from) / from) * 100;
}

function avg(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function median(values: number[]): number | null {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid]! : (xs[mid - 1]! + xs[mid]!) / 2;
}

function std(values: number[]): number {
  if (values.length < 2) return 0;
  const m = avg(values);
  return Math.sqrt(avg(values.map((v) => (v - m) ** 2)));
}

function fmtPct(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtUsd(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `$${Math.round(value).toLocaleString()}`;
}

function fmtSol(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)} SOL`;
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

async function fetchJsonCached<T>(key: string, fetcher: () => Promise<T>): Promise<T> {
  const cached = await cacheGet<T>(key);
  if (cached) return cached;
  const value = await fetcher();
  await cacheSet(key, value);
  return value;
}

async function fetchAddressTxs(address: string, limit: number, before?: string): Promise<HeliusTx[]> {
  const url = heliusAddressTransactionsUrl(address, before, limit);
  if (!url) throw new Error("HELIUS_API_KEY is not set");
  const response = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 220)}`);
  return (await response.json()) as HeliusTx[];
}

async function fetchPagedAddressTxs(address: string, maxTxs: number): Promise<HeliusTx[]> {
  return fetchJsonCached(`wallet-${address}-${maxTxs}.json`, async () => {
    const all: HeliusTx[] = [];
    let before: string | undefined;
    while (all.length < maxTxs) {
      const batch = await fetchAddressTxs(address, Math.min(100, maxTxs - all.length), before);
      if (batch.length === 0) break;
      all.push(...batch);
      before = batch[batch.length - 1]?.signature;
      await sleep(650);
    }
    return all;
  });
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, { raw: number; decimals: number }>();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      if (!mint || mint === WRAPPED_SOL_MINT) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      const decimals = change.rawTokenAmount?.decimals ?? 0;
      if (!Number.isFinite(raw) || raw === 0) continue;
      const prev = tokenNet.get(mint) ?? { raw: 0, decimals };
      prev.raw += raw;
      prev.decimals = decimals;
      tokenNet.set(mint, prev);
    }
  }

  let mint: string | null = null;
  let tokenRaw = 0;
  let decimals = 0;
  for (const [candidate, net] of tokenNet) {
    if (Math.abs(net.raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = net.raw;
      decimals = net.decimals;
    }
  }

  if (!mint || tokenRaw === 0 || nativeNet === 0) return null;
  if (nativeNet < 0 && tokenRaw > 0) {
    return { signature: tx.signature, timestamp: tx.timestamp ?? 0, side: "buy", mint, solLamports: Math.abs(nativeNet), tokenRaw: Math.abs(tokenRaw), decimals };
  }
  if (nativeNet > 0 && tokenRaw < 0) {
    return { signature: tx.signature, timestamp: tx.timestamp ?? 0, side: "sell", mint, solLamports: nativeNet, tokenRaw: Math.abs(tokenRaw), decimals };
  }
  return null;
}

function buildCampaigns(swaps: Swap[]): Campaign[] {
  const byMint = new Map<string, Campaign>();
  for (const swap of swaps.sort((a, b) => a.timestamp - b.timestamp)) {
    let c = byMint.get(swap.mint);
    if (!c) {
      if (swap.side !== "buy") continue;
      c = { mint: swap.mint, firstBuy: swap, buys: [], sells: [], spentLamports: 0, boughtRaw: 0, receivedLamports: 0, soldRaw: 0, decimals: swap.decimals };
      byMint.set(swap.mint, c);
    }
    if (swap.side === "buy") {
      c.buys.push(swap);
      c.spentLamports += swap.solLamports;
      c.boughtRaw += swap.tokenRaw;
    } else {
      c.sells.push(swap);
      c.firstSell ??= swap;
      c.receivedLamports += swap.solLamports;
      c.soldRaw += swap.tokenRaw;
    }
  }
  return [...byMint.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

async function fetchGeckoOhlcv(poolAddress: string, entryTs: number, horizonHours: number): Promise<Candle[]> {
  const untilTs = entryTs + horizonHours * 3600;
  const key = `gecko-${poolAddress}-${entryTs}-${horizonHours}.json`;
  return fetchJsonCached(key, async () => {
    const rows: Candle[] = [];
    let before = untilTs + 60;
    for (let page = 0; page < 8; page++) {
      const url =
        `${GECKO_BASE}/networks/solana/pools/${poolAddress}/ohlcv/minute` +
        `?aggregate=1&before_timestamp=${before}&limit=1000&currency=usd`;
      const response = await fetch(url, { signal: AbortSignal.timeout(12_000) });
      if (!response.ok) throw new Error(`GeckoTerminal ${response.status}: ${(await response.text()).slice(0, 180)}`);
      const json = (await response.json()) as { data?: { attributes?: { ohlcv_list?: number[][] } } };
      const batch = json.data?.attributes?.ohlcv_list ?? [];
      if (batch.length === 0) break;
      for (const row of batch) {
        const [ts, open, high, low, close, volume] = row;
        if ([ts, open, high, low, close].some((v) => typeof v !== "number")) continue;
        if (ts >= entryTs - 300 && ts <= untilTs) rows.push({ ts, open, high, low, close, volume: volume ?? 0 });
      }
      const oldest = Math.min(...batch.map((row) => row[0] ?? before));
      if (!Number.isFinite(oldest) || oldest <= entryTs - 300) break;
      before = oldest - 1;
      await sleep(2_500);
    }
    return rows.sort((a, b) => a.ts - b.ts);
  });
}

async function fetchMoralisOhlcv(poolAddress: string, entryTs: number, horizonHours: number): Promise<Candle[]> {
  if (!MORALIS_API_KEY) throw new Error("MORALIS_API_KEY is not set");
  const untilTs = entryTs + horizonHours * 3600;
  const key = `moralis-${poolAddress}-${entryTs}-${horizonHours}.json`;
  return fetchJsonCached(key, async () => {
    const rows: Candle[] = [];
    let cursor: string | undefined;
    for (let page = 0; page < 20; page++) {
      const params = new URLSearchParams({
        fromDate: String(entryTs - 300),
        toDate: String(untilTs),
        timeframe: horizonHours <= 6 ? "1min" : horizonHours <= 72 ? "5min" : "1h",
        currency: "usd",
        limit: "1000",
      });
      if (cursor) params.set("cursor", cursor);
      const response = await fetch(`${MORALIS_BASE}/token/mainnet/pairs/${poolAddress}/ohlcv?${params}`, {
        headers: { "X-Api-Key": MORALIS_API_KEY },
        signal: AbortSignal.timeout(15_000),
      });
      if (!response.ok) throw new Error(`Moralis ${response.status}: ${(await response.text()).slice(0, 180)}`);
      const json = (await response.json()) as {
        cursor?: string | null;
        result?: Array<{ timestamp?: string; open?: number; high?: number; low?: number; close?: number; volume?: number }>;
      };
      for (const item of json.result ?? []) {
        const ts = item.timestamp ? Math.floor(new Date(item.timestamp).getTime() / 1000) : NaN;
        if (!Number.isFinite(ts) || item.open == null || item.high == null || item.low == null || item.close == null) continue;
        if (ts >= entryTs - 300 && ts <= untilTs) {
          rows.push({
            ts,
            open: item.open,
            high: item.high,
            low: item.low,
            close: item.close,
            volume: item.volume ?? 0,
          });
        }
      }
      cursor = json.cursor ?? undefined;
      if (!cursor) break;
      await sleep(250);
    }
    return rows.sort((a, b) => a.ts - b.ts);
  });
}

async function fetchPathOhlcv(poolAddress: string, entryTs: number, horizonHours: number): Promise<{
  provider: string;
  candles: Candle[];
}> {
  try {
    return { provider: "Moralis", candles: await fetchMoralisOhlcv(poolAddress, entryTs, horizonHours) };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    if (!QUIET) {
      console.warn(`Moralis OHLCV failed for ${poolAddress.slice(0, 8)}...: ${msg}; falling back to GeckoTerminal.`);
    }
  }
  return { provider: "GeckoTerminal", candles: await fetchGeckoOhlcv(poolAddress, entryTs, horizonHours) };
}

async function fetchSolUsd(): Promise<number | null> {
  try {
    const response = await fetch("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", {
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) return null;
    const json = (await response.json()) as { solana?: { usd?: number } };
    return typeof json.solana?.usd === "number" ? json.solana.usd : null;
  } catch {
    return null;
  }
}

function sourceExitPct(c: Campaign): number | null {
  if (c.spentLamports <= 0 || c.boughtRaw <= 0 || c.receivedLamports <= 0 || c.soldRaw <= 0) return null;
  const costSold = (c.spentLamports / c.boughtRaw) * Math.min(c.soldRaw, c.boughtRaw);
  return costSold > 0 ? ((c.receivedLamports - costSold) / costSold) * 100 : null;
}

function entryMarketCapApprox(c: Campaign, solUsd: number | null): number | null {
  if (!solUsd || c.boughtRaw <= 0 || c.spentLamports <= 0) return null;
  const uiTokens = c.boughtRaw / 10 ** c.decimals;
  if (uiTokens <= 0) return null;
  const usdPerToken = sol(c.spentLamports) * solUsd / uiTokens;
  return usdPerToken * 1_000_000_000;
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

function inferCreator(txs: HeliusTx[], mint: string): string | null {
  const createTx =
    txs.find((tx) => tx.type === "CREATE" || tx.type === "CREATE_POOL") ??
    txs.find((tx) => extractTokenWallets(tx, mint).length > 0);
  return createTx?.feePayer ?? null;
}

async function fetchMintHistoryUntilSource(mint: string, sourceBuy: Swap): Promise<HeliusTx[]> {
  const key = `mintlead-${mint}-${sourceBuy.signature}.json`;
  return fetchJsonCached(key, async () => {
    const all: HeliusTx[] = [];
    let before: string | undefined;
    while (all.length < 500) {
      const batch = await fetchAddressTxs(mint, Math.min(100, 500 - all.length), before);
      if (batch.length === 0) break;
      all.push(...batch);
      const oldest = batch[batch.length - 1]?.timestamp ?? 0;
      if (all.some((tx) => tx.signature === sourceBuy.signature) || oldest < sourceBuy.timestamp - 180) break;
      before = batch[batch.length - 1]?.signature;
      await sleep(650);
    }
    return all.sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
  });
}

function profileLead(mint: string, txs: HeliusTx[], sourceWallet: string, sourceBuy: Swap): LeadProfile {
  const firstSeenAt = txs[0]?.timestamp ?? sourceBuy.timestamp;
  const buys: Array<{ wallet: string; signature: string; timestamp: number; solLamports: number }> = [];
  for (const tx of txs) {
    for (const wallet of extractTokenWallets(tx, mint)) {
      const swap = extractSwap(tx, wallet);
      if (swap?.mint === mint && swap.side === "buy") {
        buys.push({ wallet, signature: swap.signature, timestamp: swap.timestamp, solLamports: swap.solLamports });
      }
    }
  }
  buys.sort((a, b) => a.timestamp - b.timestamp || b.solLamports - a.solLamports);
  const sourceOrder = buys.findIndex((b) => b.signature === sourceBuy.signature || b.wallet === sourceWallet) + 1 || null;
  const earlier = buys.filter((b) => b.timestamp <= sourceBuy.timestamp && b.wallet !== sourceWallet);
  const byWallet = new Map<string, number>();
  for (const b of earlier) byWallet.set(b.wallet, (byWallet.get(b.wallet) ?? 0) + b.solLamports);
  const [topEarlierBuyer, topEarlierBuyerLamports = 0] = [...byWallet.entries()].sort((a, b) => b[1] - a[1])[0] ?? [null, 0];
  return {
    creator: inferCreator(txs, mint),
    sourceDelaySec: Math.max(0, sourceBuy.timestamp - firstSeenAt),
    sourceOrder,
    earlierBuys: earlier.length,
    earlierBuySol: sol(earlier.reduce((sum, b) => sum + b.solLamports, 0)),
    topEarlierBuyer,
    topEarlierBuyerSol: sol(topEarlierBuyerLamports),
  };
}

function simulateFixed(row: PathRow, tp: number, sl: number): number {
  for (const c of row.candles.filter((x) => x.ts >= row.entryTs)) {
    const lo = pct(row.candles[0]!.open, c.low);
    const hi = pct(row.candles[0]!.open, c.high);
    if (lo <= -sl && hi >= tp) return -sl;
    if (lo <= -sl) return -sl;
    if (hi >= tp) return tp;
  }
  return pct(row.candles[0]!.open, row.candles[row.candles.length - 1]!.close);
}

function simulateTrail(row: PathRow, arm: number, trailGiveback: number, emergencySl: number): number {
  const entry = row.candles[0]!.open;
  let peakPct = 0;
  for (const c of row.candles.filter((x) => x.ts >= row.entryTs)) {
    const lo = pct(entry, c.low);
    const hi = pct(entry, c.high);
    peakPct = Math.max(peakPct, hi);
    if (lo <= -emergencySl) return -emergencySl;
    if (peakPct >= arm && lo <= peakPct - trailGiveback) return peakPct - trailGiveback;
  }
  return pct(entry, row.candles[row.candles.length - 1]!.close);
}

function simulateTime(row: PathRow, minutes: number): number {
  const entry = row.candles[0]!.open;
  const candle = row.candles.find((c) => c.ts >= row.entryTs + minutes * 60) ?? row.candles[row.candles.length - 1]!;
  return pct(entry, candle.close);
}

function summarizeStrategy(name: string, returns: number[]) {
  const wins = returns.filter((r) => r > 0);
  const losses = returns.filter((r) => r < 0);
  const profit = wins.reduce((s, r) => s + r, 0);
  const loss = Math.abs(losses.reduce((s, r) => s + r, 0));
  const sigma = std(returns);
  return {
    name,
    trades: returns.length,
    avgPct: avg(returns),
    medianPct: median(returns),
    winRatePct: (wins.length / Math.max(1, returns.length)) * 100,
    profitFactor: loss > 0 ? profit / loss : profit > 0 ? Infinity : 0,
    sharpeProxy: sigma > 0 ? avg(returns) / sigma : 0,
    totalPct: returns.reduce((s, r) => s + r, 0),
  };
}

function makeMarkdown(wallet: string, out: {
  sample: Record<string, unknown>;
  performance: Record<string, unknown>;
  pathStats: Record<string, unknown>;
  strategies: ReturnType<typeof summarizeStrategy>[];
  leadStats: Record<string, unknown>;
  rows: PathRow[];
}): string {
  const lines = [
    `# Path-Based Wallet Audit`,
    ``,
    `Wallet: \`${wallet}\``,
    `Generated: ${new Date().toISOString()}`,
    ``,
    `## Methodology`,
    ``,
    `This audit separates realized wallet PnL from entry-path quality. It reconstructs wallet buys/sells from Helius enhanced transaction deltas, fetches post-entry OHLCV from Moralis first with GeckoTerminal fallback, then simulates exits after the source entry. Entry market cap is an approximation from the wallet fill price assuming 1B token supply; use it as a range check, not an exact historical MC.`,
    ``,
    `## Coverage`,
    ``,
    ...Object.entries(out.sample).map(([k, v]) => `- ${k}: ${String(v)}`),
    ``,
    `## Wallet PnL`,
    ``,
    ...Object.entries(out.performance).map(([k, v]) => `- ${k}: ${String(v)}`),
    ``,
    `## Entry Path Stats`,
    ``,
    ...Object.entries(out.pathStats).map(([k, v]) => `- ${k}: ${String(v)}`),
    ``,
    `## Strategy Backtest`,
    ``,
    `| Strategy | Trades | Avg | Median | Win Rate | Profit Factor | Sharpe Proxy |`,
    `|---|---:|---:|---:|---:|---:|---:|`,
    ...out.strategies.map((s) => `| ${s.name} | ${s.trades} | ${fmtPct(s.avgPct)} | ${fmtPct(s.medianPct)} | ${fmtPct(s.winRatePct)} | ${s.profitFactor.toFixed(2)} | ${s.sharpeProxy.toFixed(2)} |`),
    ``,
    `## Lead Wallet / Sniping Clues`,
    ``,
    ...Object.entries(out.leadStats).map(([k, v]) => `- ${k}: ${String(v)}`),
    ``,
    `## Recent Measured Entries`,
    ``,
    `| Token | Buy SOL | Approx Entry MC | MFE | MAE | 2x Time | Source Exit | Earlier Buys | Top Earlier Buyer |`,
    `|---|---:|---:|---:|---:|---:|---:|---:|---|`,
    ...out.rows.slice(0, 40).map((r) =>
      `| ${r.symbol} (${short(r.mint)}) | ${r.sourceBuySol.toFixed(4)} | ${fmtUsd(r.entryMarketCapUsdApprox)} | ${fmtPct(r.mfePct)} | ${fmtPct(r.maePct)} | ${r.timeTo2xSec == null ? "-" : `${Math.round(r.timeTo2xSec / 60)}m`} | ${fmtPct(r.sourceExitPct)} | ${r.lead?.earlierBuys ?? "-"} | ${short(r.lead?.topEarlierBuyer)} |`
    ),
    ``,
  ];
  return lines.join("\n");
}

async function writeAuditFiles(wallet: string, output: {
  sample: Record<string, unknown>;
  performance: Record<string, unknown>;
  pathStats: Record<string, unknown>;
  strategies: ReturnType<typeof summarizeStrategy>[];
  leadStats: Record<string, unknown>;
  rows: PathRow[];
}): Promise<void> {
  const base = path.resolve(process.cwd(), "data", `path-wallet-audit-${wallet}`);
  await mkdir(path.dirname(base), { recursive: true });
  await writeFile(`${base}.json`, JSON.stringify(output, null, 2), "utf8");
  await writeFile(`${base}.md`, makeMarkdown(wallet, output), "utf8");
}

function buildOutput(
  wallet: string,
  txs: HeliusTx[],
  swaps: Swap[],
  campaigns: Campaign[],
  rows: PathRow[],
  horizonHours: number,
  solUsd: number | null,
  partial: boolean
) {
  const closed = campaigns.filter((c) => c.receivedLamports > 0 && c.soldRaw > 0);
  const realized = closed.map((c) => {
    const soldCost = (c.spentLamports / Math.max(1, c.boughtRaw)) * Math.min(c.soldRaw, c.boughtRaw);
    return c.receivedLamports - soldCost;
  });
  const sourceExits = rows.map((r) => r.sourceExitPct).filter((v): v is number => v != null);
  const leadRows = rows.filter((r) => r.lead);
  const topEarlierCounts = new Map<string, number>();
  for (const r of leadRows) {
    if (r.lead?.topEarlierBuyer) topEarlierCounts.set(r.lead.topEarlierBuyer, (topEarlierCounts.get(r.lead.topEarlierBuyer) ?? 0) + 1);
  }
  const repeatedTopEarlier = [...topEarlierCounts.entries()].filter(([, count]) => count > 1).sort((a, b) => b[1] - a[1]);
  const strategies = [
    summarizeStrategy("Mirror source exit", sourceExits),
    summarizeStrategy("TP30 SL25", rows.map((r) => simulateFixed(r, 30, 25))),
    summarizeStrategy("TP50 SL30", rows.map((r) => simulateFixed(r, 50, 30))),
    summarizeStrategy("TP100 SL35", rows.map((r) => simulateFixed(r, 100, 35))),
    summarizeStrategy("Trail arm45 give30 SL35", rows.map((r) => simulateTrail(r, 45, 30, 35))),
    summarizeStrategy("Trail arm65 give35 SL35", rows.map((r) => simulateTrail(r, 65, 35, 35))),
    summarizeStrategy("Time exit 30m", rows.map((r) => simulateTime(r, 30))),
    summarizeStrategy("Time exit 1h", rows.map((r) => simulateTime(r, 60))),
    summarizeStrategy("Time exit 4h", rows.map((r) => simulateTime(r, 240))),
  ].sort((a, b) => b.avgPct - a.avgPct);

  return {
    sample: {
      partial,
      txsFetched: txs.length,
      swaps: swaps.length,
      campaigns: campaigns.length,
      measuredEntries: rows.length,
      oldestTx: new Date(Math.min(...txs.map((tx) => (tx.timestamp ?? 0) * 1000))).toISOString(),
      newestTx: new Date(Math.max(...txs.map((tx) => (tx.timestamp ?? 0) * 1000))).toISOString(),
      horizonHours,
      solUsdUsed: solUsd?.toFixed(2) ?? "unavailable",
    },
    performance: {
      realizedPnlSol: fmtSol(sol(realized.reduce((s, v) => s + v, 0))),
      closedCampaigns: closed.length,
      realizedWinRate: fmtPct((realized.filter((v) => v > 0).length / Math.max(1, realized.length)) * 100),
      avgSourceExit: fmtPct(avg(sourceExits)),
      medianSourceExit: fmtPct(median(sourceExits)),
    },
    pathStats: {
      hit30: `${rows.filter((r) => r.mfePct >= 30).length}/${rows.length}`,
      hit50: `${rows.filter((r) => r.mfePct >= 50).length}/${rows.length}`,
      hit100: `${rows.filter((r) => r.mfePct >= 100).length}/${rows.length}`,
      hit200: `${rows.filter((r) => r.mfePct >= 200).length}/${rows.length}`,
      medianMfe: fmtPct(median(rows.map((r) => r.mfePct))),
      medianMae: fmtPct(median(rows.map((r) => r.maePct))),
      medianEntryMcApprox: fmtUsd(median(rows.map((r) => r.entryMarketCapUsdApprox ?? NaN))),
      medianTimeTo2x: rows.some((r) => r.timeTo2xSec != null) ? `${Math.round((median(rows.map((r) => r.timeTo2xSec ?? NaN)) ?? 0) / 60)}m` : "-",
    },
    strategies,
    leadStats: {
      profiledLeadEntries: leadRows.length,
      medianSourceOrder: String(median(leadRows.map((r) => r.lead?.sourceOrder ?? NaN)) ?? "-"),
      medianSourceDelaySec: String(median(leadRows.map((r) => r.lead?.sourceDelaySec ?? NaN)) ?? "-"),
      medianEarlierBuys: String(median(leadRows.map((r) => r.lead?.earlierBuys ?? NaN)) ?? "-"),
      repeatedTopEarlierBuyers: repeatedTopEarlier.slice(0, 8).map(([w, count]) => `${short(w)} ${count}x`).join(", ") || "none",
    },
    rows,
  };
}

async function main(): Promise<void> {
  const wallet = process.argv[2] ?? "";
  const maxTxs = Number(process.argv[3] ?? "5000");
  const entryLimit = Number(process.argv[4] ?? "80");
  const horizonHours = Number(process.argv[5] ?? "24");
  const leadLimit = Number(process.argv[6] ?? "20");
  const perEntrySleepMs = Number(process.env.PATH_AUDIT_SLEEP_MS ?? "250");
  if (!wallet) {
    throw new Error("usage: tsx src/pathBasedWalletAudit.ts WALLET [maxTxs] [entryLimit] [horizonHours] [leadLimit]");
  }

  const txs = await fetchPagedAddressTxs(wallet, maxTxs);
  const swaps = txs.map((tx) => extractSwap(tx, wallet)).filter((swap): swap is Swap => swap != null);
  const campaigns = buildCampaigns(swaps);
  const solUsd = await fetchSolUsd();
  const rows: PathRow[] = [];
  const candidates = campaigns.slice(0, entryLimit);
  if (!QUIET) {
    console.log(
      `Path audit ${short(wallet)}: txs=${txs.length}, campaigns=${campaigns.length}, entries=${candidates.length}, leadLimit=${leadLimit}`
    );
  }

  for (const [index, c] of candidates.entries()) {
    try {
      const pair = await getTokenPairInfo(c.mint);
      if (!pair?.pairAddress) continue;
      const { candles } = await fetchPathOhlcv(pair.pairAddress, c.firstBuy.timestamp, horizonHours);
      const after = candles.filter((x) => x.ts >= c.firstBuy.timestamp);
      if (after.length === 0 || after[0]!.open <= 0) continue;
      const entry = after[0]!.open;
      const high = Math.max(...after.map((x) => x.high));
      const low = Math.min(...after.map((x) => x.low));
      const hit2x = after.find((x) => pct(entry, x.high) >= 100);
      let lead: LeadProfile | null = null;
      if (rows.length < leadLimit) {
        try {
          lead = profileLead(c.mint, await fetchMintHistoryUntilSource(c.mint, c.firstBuy), wallet, c.firstBuy);
        } catch {
          lead = null;
        }
      }
      rows.push({
        mint: c.mint,
        symbol: pair.baseToken.symbol || short(c.mint),
        entryTs: c.firstBuy.timestamp,
        sourceBuySol: sol(c.firstBuy.solLamports),
        sourceExitPct: sourceExitPct(c),
        entryMarketCapUsdApprox: entryMarketCapApprox(c, solUsd),
        pre5mVolumeUsd: candles.filter((x) => x.ts >= c.firstBuy.timestamp - 300 && x.ts < c.firstBuy.timestamp).reduce((s, x) => s + x.volume, 0),
        mfePct: pct(entry, high),
        maePct: pct(entry, low),
        timeTo2xSec: hit2x ? hit2x.ts - c.firstBuy.timestamp : null,
        candles: after,
        lead,
      });
      if (!QUIET) {
        console.log(
          `  ${index + 1}/${candidates.length} ${pair.baseToken.symbol || short(c.mint)} measured=${rows.length} MFE=${fmtPct(
            pct(entry, high)
          )} sourceExit=${fmtPct(sourceExitPct(c))}`
        );
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (!QUIET) console.warn(`Skipping ${short(c.mint)}: ${msg}`);
    }
    if (rows.length > 0 && rows.length % 10 === 0) {
      await writeAuditFiles(wallet, buildOutput(wallet, txs, swaps, campaigns, rows, horizonHours, solUsd, true));
    }
    await sleep(perEntrySleepMs);
  }
  const output = buildOutput(wallet, txs, swaps, campaigns, rows, horizonHours, solUsd, false);
  await writeAuditFiles(wallet, output);
  if (QUIET) {
    console.log(
      `Path audit ${short(wallet)} complete: measured=${output.sample.measuredEntries}, ` +
        `oldest=${output.sample.oldestTx}, newest=${output.sample.newestTx}`
    );
  } else {
    console.log(JSON.stringify(output, null, 2));
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
