import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const MORALIS_BASE = "https://solana-gateway.moralis.io";
const MORALIS_API_KEY = process.env.MORALIS_API_KEY ?? "";
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-quant-audit-cache");

type HeliusTx = {
  signature: string;
  timestamp?: number;
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
  signature: string;
  timestamp: number;
  side: "buy" | "sell";
  mint: string;
  solLamports: number;
  tokenRaw: number;
};

type Campaign = {
  mint: string;
  firstBuy: Swap;
  lastSell?: Swap;
  spent: number;
  boughtRaw: number;
  received: number;
  soldRaw: number;
  buys: Swap[];
  sells: Swap[];
};

type Candle = { ts: number; open: number; high: number; low: number; close: number; volume: number };
type EntryLab = {
  mint: string;
  symbol: string;
  entryTs: number;
  entrySol: number;
  entryPrice: number;
  candles: Candle[];
  campaign?: Campaign;
  pre5mVolumeUsd: number;
  pre5mMomentumPct: number | null;
  currentLiquidityUsd: number | null;
  currentMarketCapUsd: number | null;
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

function quantile(values: number[], q: number): number | null {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  return xs[Math.min(xs.length - 1, Math.max(0, Math.floor((xs.length - 1) * q)))]!;
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
  if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 180)}`);
  return (await response.json()) as HeliusTx[];
}

async function fetchPagedWalletTxs(wallet: string, maxTxs: number): Promise<HeliusTx[]> {
  const key = `wallet-${wallet}-${maxTxs}.json`;
  const cached = await cacheGet<HeliusTx[]>(key);
  if (cached) return cached;
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(wallet, Math.min(100, maxTxs - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  await cacheSet(key, all);
  return all;
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
      if (!mint || !Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
    }
  }
  let mint: string | null = null;
  let tokenRaw = 0;
  for (const [candidate, raw] of tokenNet) {
    if (candidate === WRAPPED_SOL_MINT) continue;
    if (Math.abs(raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = raw;
    }
  }
  const wsolNet = tokenNet.get(WRAPPED_SOL_MINT) ?? 0;
  const quoteLamports =
    tokenRaw > 0 && wsolNet < 0
      ? Math.abs(wsolNet)
      : tokenRaw < 0 && wsolNet > 0
      ? wsolNet
      : tokenRaw > 0 && nativeNet < 0
      ? Math.abs(nativeNet)
      : tokenRaw < 0 && nativeNet > 0
      ? nativeNet
      : 0;
  if (!mint || tokenRaw === 0 || quoteLamports <= 0) return null;
  return {
    signature: tx.signature,
    timestamp: tx.timestamp ?? 0,
    side: tokenRaw > 0 ? "buy" : "sell",
    mint,
    solLamports: quoteLamports,
    tokenRaw: Math.abs(tokenRaw),
  };
}

function buildCampaigns(swaps: Swap[]): Campaign[] {
  const campaigns = new Map<string, Campaign>();
  for (const swap of swaps.sort((a, b) => a.timestamp - b.timestamp)) {
    let c = campaigns.get(swap.mint);
    if (!c) {
      if (swap.side !== "buy") continue;
      c = { mint: swap.mint, firstBuy: swap, spent: 0, boughtRaw: 0, received: 0, soldRaw: 0, buys: [], sells: [] };
      campaigns.set(swap.mint, c);
    }
    if (swap.side === "buy") {
      c.spent += swap.solLamports;
      c.boughtRaw += swap.tokenRaw;
      c.buys.push(swap);
    } else {
      c.received += swap.solLamports;
      c.soldRaw += swap.tokenRaw;
      c.sells.push(swap);
      c.lastSell = swap;
    }
  }
  return [...campaigns.values()];
}

async function fetchMoralisOhlcv(poolAddress: string, fromTs: number, toTs: number): Promise<Candle[]> {
  if (!MORALIS_API_KEY) throw new Error("MORALIS_API_KEY is not set");
  const key = `ohlcv-${poolAddress}-${fromTs}-${toTs}.json`;
  const cached = await cacheGet<Candle[]>(key);
  if (cached) return cached;
  const span = toTs - fromTs;
  const timeframe = span <= 6 * 3600 ? "1min" : span <= 3 * 86400 ? "5min" : "1h";
  const rows: Candle[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < 20; page++) {
    const params = new URLSearchParams({
      fromDate: String(fromTs),
      toDate: String(toTs),
      timeframe,
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
      rows.push({ ts, open: item.open, high: item.high, low: item.low, close: item.close, volume: item.volume ?? 0 });
    }
    cursor = json.cursor ?? undefined;
    if (!cursor) break;
    await sleep(250);
  }
  const out = rows.filter((row) => row.ts >= fromTs && row.ts <= toTs).sort((a, b) => a.ts - b.ts);
  await cacheSet(key, out);
  return out;
}

function candleAtOrAfter(candles: Candle[], ts: number): Candle | null {
  return candles.find((c) => c.ts >= ts) ?? candles[candles.length - 1] ?? null;
}

function priceAt(candles: Candle[], ts: number): number | null {
  return candleAtOrAfter(candles, ts)?.close ?? null;
}

function returnsPath(row: EntryLab): { mfe: number; mae: number } {
  const post = row.candles.filter((c) => c.ts >= row.entryTs);
  const high = Math.max(...post.map((c) => c.high));
  const low = Math.min(...post.map((c) => c.low));
  return { mfe: pct(row.entryPrice, high), mae: pct(row.entryPrice, low) };
}

function fixedTpSl(row: EntryLab, tp: number, sl: number): number {
  for (const c of row.candles.filter((x) => x.ts >= row.entryTs)) {
    const lo = pct(row.entryPrice, c.low);
    const hi = pct(row.entryPrice, c.high);
    if (lo <= -sl && hi >= tp) return -sl;
    if (lo <= -sl) return -sl;
    if (hi >= tp) return tp;
  }
  const last = row.candles[row.candles.length - 1]!;
  return pct(row.entryPrice, last.close);
}

function timeExit(row: EntryLab, minutes: number): number {
  const p = priceAt(row.candles, row.entryTs + minutes * 60) ?? row.candles[row.candles.length - 1]!.close;
  return pct(row.entryPrice, p);
}

function trailing(row: EntryLab, trailPct: number): number {
  let peak = row.entryPrice;
  for (const c of row.candles.filter((x) => x.ts >= row.entryTs)) {
    peak = Math.max(peak, c.high);
    const stop = peak * (1 - trailPct / 100);
    if (c.low <= stop) return pct(row.entryPrice, stop);
  }
  return pct(row.entryPrice, row.candles[row.candles.length - 1]!.close);
}

function sourceExit(row: EntryLab): number {
  const c = row.campaign;
  if (!c || c.spent <= 0 || c.received <= 0 || c.soldRaw <= 0) return timeExit(row, 24 * 60);
  const costBasisSold = c.spent * (Math.min(c.soldRaw, c.boughtRaw) / c.boughtRaw);
  return costBasisSold > 0 ? ((c.received - costBasisSold) / costBasisSold) * 100 : 0;
}

function halfTpHalfSource(row: EntryLab): number {
  const hit30 = row.candles.filter((c) => c.ts >= row.entryTs).some((c) => pct(row.entryPrice, c.high) >= 30);
  return hit30 ? 0.5 * 30 + 0.5 * sourceExit(row) : sourceExit(row);
}

function maxDrawdown(returns: number[]): number {
  let equity = 0;
  let peak = 0;
  let dd = 0;
  for (const r of returns) {
    equity += r;
    peak = Math.max(peak, equity);
    dd = Math.min(dd, equity - peak);
  }
  return dd;
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
    totalRoiPct: returns.reduce((s, r) => s + r, 0),
    winRatePct: (wins.length / Math.max(1, returns.length)) * 100,
    avgPct: avg(returns),
    medianPct: median(returns),
    maxDrawdownPct: maxDrawdown(returns),
    sharpe: sigma > 0 ? avg(returns) / sigma : 0,
    profitFactor: loss > 0 ? profit / loss : profit > 0 ? Infinity : 0,
    evPct: avg(returns),
  };
}

async function buildLabs(campaigns: Campaign[], limit: number): Promise<EntryLab[]> {
  const labs: EntryLab[] = [];
  for (const c of campaigns.sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp).slice(0, limit)) {
    try {
      const pair = await getTokenPairInfo(c.mint);
      if (!pair?.pairAddress) continue;
      const from = Math.max(0, c.firstBuy.timestamp - 30 * 60);
      const to = c.firstBuy.timestamp + 24 * 3600;
      const candles = await fetchMoralisOhlcv(pair.pairAddress, from, to);
      const entryCandle = candleAtOrAfter(candles, c.firstBuy.timestamp);
      if (!entryCandle || entryCandle.open <= 0) continue;
      const pre5 = candles.filter((x) => x.ts >= c.firstBuy.timestamp - 300 && x.ts < c.firstBuy.timestamp);
      labs.push({
        mint: c.mint,
        symbol: pair.baseToken.symbol || short(c.mint),
        entryTs: c.firstBuy.timestamp,
        entrySol: sol(c.firstBuy.solLamports),
        entryPrice: entryCandle.open,
        candles: candles.filter((x) => x.ts >= c.firstBuy.timestamp),
        campaign: c,
        pre5mVolumeUsd: pre5.reduce((s, x) => s + x.volume, 0),
        pre5mMomentumPct: pre5.length ? pct(pre5[0]!.open, entryCandle.open) : null,
        currentLiquidityUsd: pair.liquidityUsd,
        currentMarketCapUsd: pair.marketCapUsd,
      });
    } catch (err) {
      console.warn(`skip ${short(c.mint)}: ${err instanceof Error ? err.message : String(err)}`);
    }
    await sleep(200);
  }
  return labs;
}

async function main(): Promise<void> {
  const wallet = process.argv[2] ?? "";
  const maxTxs = Number(process.argv[3] ?? "5000");
  const entryLimit = Number(process.argv[4] ?? "220");
  if (!wallet) throw new Error("usage: tsx src/walletQuantAudit.ts WALLET [maxTxs] [entryLimit]");

  const txs = await fetchPagedWalletTxs(wallet, maxTxs);
  const swaps = txs.map((tx) => extractSwap(tx, wallet)).filter((x): x is Swap => x != null);
  const campaigns = buildCampaigns(swaps);
  const closed = campaigns.filter((c) => c.spent > 0 && c.received > 0 && c.soldRaw > 0);
  const realizedPnls = closed.map((c) => c.received - c.spent * (Math.min(c.soldRaw, c.boughtRaw) / c.boughtRaw));
  const realizedRois = closed.map((c) => sourceExit({ campaign: c } as EntryLab));
  const holding = closed.map((c) => (c.lastSell!.timestamp - c.firstBuy.timestamp) / 60);
  const labs = await buildLabs(campaigns, entryLimit);
  const paths = labs.map((row) => ({ row, ...returnsPath(row) }));
  const open = campaigns.filter((c) => c.spent > 0 && c.soldRaw < c.boughtRaw);
  const openApprox = open
    .map((c) => labs.find((l) => l.mint === c.mint))
    .filter((l): l is EntryLab => Boolean(l))
    .map((l) => pct(l.entryPrice, l.candles[l.candles.length - 1]!.close));

  const strategies = [
    summarizeStrategy("A TP30 SL20", labs.map((r) => fixedTpSl(r, 30, 20))),
    summarizeStrategy("B TP50 SL25", labs.map((r) => fixedTpSl(r, 50, 25))),
    summarizeStrategy("C Sell When Wallet Sells", labs.map(sourceExit)),
    summarizeStrategy("D 50% at +30, 50% source exit", labs.map(halfTpHalfSource)),
    summarizeStrategy("E 20% trailing stop", labs.map((r) => trailing(r, 20))),
    summarizeStrategy("F time exit 30m", labs.map((r) => timeExit(r, 30))),
    summarizeStrategy("G time exit 1h", labs.map((r) => timeExit(r, 60))),
    summarizeStrategy("H time exit 4h", labs.map((r) => timeExit(r, 240))),
    summarizeStrategy("I time exit 24h", labs.map((r) => timeExit(r, 1440))),
  ].sort((a, b) => b.evPct - a.evPct);

  const thresholds = [10, 20, 30, 50, 100].map((t) => {
    const hits = paths.filter((p) => p.mfe >= t);
    const times = hits.map((p) => {
      const hit = p.row.candles.find((c) => pct(p.row.entryPrice, c.high) >= t);
      return hit ? hit.ts - p.row.entryTs : null;
    }).filter((x): x is number => x != null);
    return { target: `+${t}%`, probabilityPct: (hits.length / Math.max(1, paths.length)) * 100, avgTimeSec: times.length ? avg(times) : null };
  });
  const downside = [10, 20, 30].map((t) => {
    const hits = paths.filter((p) => p.mae <= -t);
    return { target: `-${t}%`, probabilityPct: (hits.length / Math.max(1, paths.length)) * 100 };
  });

  const out = {
    wallet,
    sample: {
      txsFetched: txs.length,
      swaps: swaps.length,
      buys: swaps.filter((s) => s.side === "buy").length,
      sells: swaps.filter((s) => s.side === "sell").length,
      campaigns: campaigns.length,
      closedCampaigns: closed.length,
      measuredEntries: labs.length,
      oldestTxIso: new Date(Math.min(...txs.map((tx) => (tx.timestamp ?? 0) * 1000))).toISOString(),
      newestTxIso: new Date(Math.max(...txs.map((tx) => (tx.timestamp ?? 0) * 1000))).toISOString(),
    },
    walletPerformance: {
      realizedPnlSol: sol(realizedPnls.reduce((s, p) => s + p, 0)),
      unrealizedRoiPctApproxMedian: median(openApprox),
      unrealizedOpenMeasured: openApprox.length,
      winRatePct: (realizedPnls.filter((p) => p > 0).length / Math.max(1, realizedPnls.length)) * 100,
      avgRoiPct: avg(realizedRois),
      medianRoiPct: median(realizedRois),
      avgHoldingMin: avg(holding),
      medianHoldingMin: median(holding),
      profitFactor:
        Math.abs(realizedPnls.filter((p) => p < 0).reduce((s, p) => s + p, 0)) > 0
          ? realizedPnls.filter((p) => p > 0).reduce((s, p) => s + p, 0) /
            Math.abs(realizedPnls.filter((p) => p < 0).reduce((s, p) => s + p, 0))
          : null,
      sharpe: std(realizedRois) > 0 ? avg(realizedRois) / std(realizedRois) : 0,
      maxDrawdownPct: maxDrawdown(realizedRois),
      largestWinSol: sol(Math.max(...realizedPnls)),
      largestLossSol: sol(Math.min(...realizedPnls)),
    },
    entryStats: {
      avgEntrySol: avg(labs.map((l) => l.entrySol)),
      medianEntrySol: median(labs.map((l) => l.entrySol)),
      medianPre5mVolumeUsd: median(labs.map((l) => l.pre5mVolumeUsd)),
      p75Pre5mVolumeUsd: quantile(labs.map((l) => l.pre5mVolumeUsd), 0.75),
      medianCurrentLiquidityUsd: median(labs.map((l) => l.currentLiquidityUsd ?? NaN)),
      medianCurrentMarketCapUsd: median(labs.map((l) => l.currentMarketCapUsd ?? NaN)),
      medianMfePct: median(paths.map((p) => p.mfe)),
      medianMaePct: median(paths.map((p) => p.mae)),
    },
    targetProbabilities: thresholds,
    downsideProbabilities: downside,
    strategies,
    concentration: {
      top5WinSol: sol(realizedPnls.filter((p) => p > 0).sort((a, b) => b - a).slice(0, 5).reduce((s, p) => s + p, 0)),
      totalRealizedSol: sol(realizedPnls.reduce((s, p) => s + p, 0)),
      top5WinShareOfTotalPct:
        (realizedPnls.filter((p) => p > 0).sort((a, b) => b - a).slice(0, 5).reduce((s, p) => s + p, 0) /
          Math.max(1, realizedPnls.reduce((s, p) => s + p, 0))) *
        100,
    },
    recentEntries: labs.slice(0, 20).map((l) => ({ mint: short(l.mint), symbol: l.symbol, entrySol: l.entrySol, ...returnsPath(l), pre5mVolumeUsd: l.pre5mVolumeUsd })),
  };
  console.log(JSON.stringify(out, null, 2));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
