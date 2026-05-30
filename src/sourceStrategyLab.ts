import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const MORALIS_BASE = "https://solana-gateway.moralis.io";
const MORALIS_API_KEY = process.env.MORALIS_API_KEY ?? "";
const CACHE_DIR = path.resolve(process.cwd(), "data", "strategy-lab-cache");

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
  firstSell?: Swap;
  buys: Swap[];
  sells: Swap[];
  spentLamports: number;
  boughtRaw: number;
  receivedLamports: number;
  soldRaw: number;
};

type Candle = {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type LabRow = {
  mint: string;
  symbol: string;
  buyAt: number;
  entryPrice: number | null;
  sourceBuySol: number;
  sourceExitPct: number | null;
  mfePct: number | null;
  maePct: number | null;
  hit30: boolean;
  timeTo30Sec: number | null;
  drawdownBefore30Pct: number | null;
  athAfterSourceExitPct: number | null;
  sourceExitedBeforeAth: boolean | null;
  currentLiquidityUsd: number | null;
  currentMarketCapUsd: number | null;
  pre5mMomentumPct: number | null;
  pre5mVolumeUsd: number;
  tokenAgeAtEntrySec: number | null;
  candles: Candle[];
};

type SimResult = {
  name: string;
  trades: number;
  wins: number;
  losses: number;
  avgPct: number;
  medianPct: number | null;
  expectancyPct: number;
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

function fmtPct(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function median(values: number[]): number | null {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  return sorted[Math.floor(sorted.length / 2)] ?? null;
}

function avg(values: number[]): number {
  const xs = values.filter(Number.isFinite);
  return xs.reduce((a, b) => a + b, 0) / Math.max(1, xs.length);
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

function addTokenNet(tokenNet: Map<string, number>, mint: string | undefined, raw: number): void {
  if (!mint || !Number.isFinite(raw) || raw === 0) return;
  tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, number>();
  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      addTokenNet(tokenNet, change.mint, Number(change.rawTokenAmount?.tokenAmount ?? "0"));
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
  for (const swap of swaps) {
    let campaign = campaigns.get(swap.mint);
    if (!campaign) {
      if (swap.side !== "buy") continue;
      campaign = {
        mint: swap.mint,
        firstBuy: swap,
        buys: [],
        sells: [],
        spentLamports: 0,
        boughtRaw: 0,
        receivedLamports: 0,
        soldRaw: 0,
      };
      campaigns.set(swap.mint, campaign);
    }
    if (swap.side === "buy") {
      campaign.buys.push(swap);
      campaign.spentLamports += swap.solLamports;
      campaign.boughtRaw += swap.tokenRaw;
    } else {
      campaign.sells.push(swap);
      campaign.firstSell ??= swap;
      campaign.receivedLamports += swap.solLamports;
      campaign.soldRaw += swap.tokenRaw;
    }
  }
  return [...campaigns.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

async function fetchMoralisOhlcv(poolAddress: string, fromTs: number, toTs: number): Promise<Candle[]> {
  if (!MORALIS_API_KEY) throw new Error("MORALIS_API_KEY is not set");
  const cacheKey = `moralis-${poolAddress}-${fromTs}-${toTs}.json`;
  const cached = await cacheGet<Candle[]>(cacheKey);
  if (cached) return cached;

  const span = Math.max(0, toTs - fromTs);
  const timeframe = span <= 6 * 60 * 60 ? "1min" : span <= 3 * 24 * 60 * 60 ? "5min" : "1h";
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
    const url = `${MORALIS_BASE}/token/mainnet/pairs/${poolAddress}/ohlcv?${params.toString()}`;
    const response = await fetch(url, {
      headers: { "X-Api-Key": MORALIS_API_KEY },
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Moralis ${response.status}: ${text.slice(0, 180)}`);
    }
    const json = (await response.json()) as {
      cursor?: string | null;
      result?: Array<{ timestamp?: string; open?: number; high?: number; low?: number; close?: number; volume?: number }>;
    };
    for (const item of json.result ?? []) {
      const ts = item.timestamp ? Math.floor(new Date(item.timestamp).getTime() / 1000) : NaN;
      if (
        !Number.isFinite(ts) ||
        typeof item.open !== "number" ||
        typeof item.high !== "number" ||
        typeof item.low !== "number" ||
        typeof item.close !== "number"
      ) {
        continue;
      }
      rows.push({
        ts,
        open: item.open,
        high: item.high,
        low: item.low,
        close: item.close,
        volume: item.volume ?? 0,
      });
    }
    cursor = json.cursor ?? undefined;
    if (!cursor) break;
    await sleep(250);
  }
  const out = rows
    .filter((row) => row.ts >= fromTs && row.ts <= toTs)
    .sort((a, b) => a.ts - b.ts);
  await cacheSet(cacheKey, out);
  return out;
}

function candleAtOrAfter(candles: Candle[], ts: number): Candle | null {
  return candles.find((c) => c.ts >= ts) ?? candles[candles.length - 1] ?? null;
}

function pct(from: number, to: number): number {
  return ((to - from) / from) * 100;
}

function analyzeCandles(campaign: Campaign, candles: Candle[]): Omit<LabRow, "mint" | "symbol" | "sourceBuySol" | "currentLiquidityUsd" | "currentMarketCapUsd" | "candles"> {
  const entryCandle = candleAtOrAfter(candles, campaign.firstBuy.timestamp);
  const entryPrice = entryCandle?.open ?? null;
  const post = entryPrice == null ? [] : candles.filter((c) => c.ts >= campaign.firstBuy.timestamp);
  const pre5 = entryPrice == null ? [] : candles.filter((c) => c.ts >= campaign.firstBuy.timestamp - 5 * 60 && c.ts < campaign.firstBuy.timestamp);
  const firstCandle = candles[0] ?? null;
  const tokenAgeAtEntrySec = firstCandle ? Math.max(0, campaign.firstBuy.timestamp - firstCandle.ts) : null;
  const pre5mMomentumPct =
    pre5.length > 0 && entryPrice != null ? pct(pre5[0]!.open, entryPrice) : null;
  const pre5mVolumeUsd = pre5.reduce((sum, c) => sum + c.volume, 0);

  if (entryPrice == null || post.length === 0) {
    return {
      buyAt: campaign.firstBuy.timestamp,
      entryPrice,
      sourceExitPct: null,
      mfePct: null,
      maePct: null,
      hit30: false,
      timeTo30Sec: null,
      drawdownBefore30Pct: null,
      athAfterSourceExitPct: null,
      sourceExitedBeforeAth: null,
      pre5mMomentumPct,
      pre5mVolumeUsd,
      tokenAgeAtEntrySec,
    };
  }

  const peak = post.reduce((best, c) => (c.high > best.high ? c : best), post[0]!);
  const trough = post.reduce((best, c) => (c.low < best.low ? c : best), post[0]!);
  const hit30Candle = post.find((c) => pct(entryPrice, c.high) >= 30);
  const before30 = hit30Candle ? post.filter((c) => c.ts <= hit30Candle.ts) : post;
  const minBefore30 = before30.reduce((best, c) => (c.low < best.low ? c : best), before30[0]!);
  const exitPrice =
    campaign.firstSell && campaign.soldRaw > 0
      ? campaign.receivedLamports / Math.min(campaign.soldRaw, campaign.boughtRaw)
      : null;
  const entrySolPrice = campaign.spentLamports / campaign.boughtRaw;
  const sourceExitPct = exitPrice != null ? pct(entrySolPrice, exitPrice) : null;
  const afterExit =
    campaign.firstSell != null ? post.filter((c) => c.ts >= campaign.firstSell!.timestamp) : [];
  const peakAfterExit = afterExit.reduce<Candle | null>((best, c) => (!best || c.high > best.high ? c : best), null);

  return {
    buyAt: campaign.firstBuy.timestamp,
    entryPrice,
    sourceExitPct,
    mfePct: pct(entryPrice, peak.high),
    maePct: pct(entryPrice, trough.low),
    hit30: Boolean(hit30Candle),
    timeTo30Sec: hit30Candle ? hit30Candle.ts - campaign.firstBuy.timestamp : null,
    drawdownBefore30Pct: hit30Candle ? pct(entryPrice, minBefore30.low) : null,
    athAfterSourceExitPct: peakAfterExit ? pct(entryPrice, peakAfterExit.high) : null,
    sourceExitedBeforeAth:
      campaign.firstSell != null ? peak.ts > campaign.firstSell.timestamp : null,
    pre5mMomentumPct,
    pre5mVolumeUsd,
    tokenAgeAtEntrySec,
  };
}

function simulateFixed(row: LabRow, tpPct: number, slPct: number): number | null {
  if (row.entryPrice == null) return null;
  const entry = row.entryPrice;
  const post = row.candles.filter((c) => c.ts >= row.buyAt);
  for (const c of post) {
    const lowPct = pct(entry, c.low);
    const highPct = pct(entry, c.high);
    if (lowPct <= -slPct && highPct >= tpPct) return -slPct;
    if (lowPct <= -slPct) return -slPct;
    if (highPct >= tpPct) return tpPct;
  }
  const last = post[post.length - 1];
  return last ? pct(entry, last.close) : null;
}

function simulateTrailing(row: LabRow, stopLossPct: number, steps: Array<{ peak: number; floor: number }>): number | null {
  if (row.entryPrice == null) return null;
  const entry = row.entryPrice;
  let peakPct = -Infinity;
  let stopPct = -stopLossPct;
  const post = row.candles.filter((c) => c.ts >= row.buyAt);
  for (const c of post) {
    peakPct = Math.max(peakPct, pct(entry, c.high));
    for (const step of steps) {
      if (peakPct >= step.peak) stopPct = Math.max(stopPct, step.floor);
    }
    if (pct(entry, c.low) <= stopPct) return stopPct;
  }
  const last = post[post.length - 1];
  return last ? pct(entry, last.close) : null;
}

function summarizeSim(name: string, values: Array<number | null>): SimResult {
  const xs = values.filter((v): v is number => v != null && Number.isFinite(v));
  return {
    name,
    trades: xs.length,
    wins: xs.filter((v) => v > 0).length,
    losses: xs.filter((v) => v < 0).length,
    avgPct: avg(xs),
    medianPct: median(xs),
    expectancyPct: avg(xs),
  };
}

function bucketStats(rows: LabRow[], label: string, predicate: (row: LabRow) => boolean): string {
  const bucket = rows.filter(predicate);
  const measured = bucket.filter((r) => r.mfePct != null);
  const hit30 = measured.filter((r) => r.hit30);
  return `${label}: n=${measured.length}, hit30=${hit30.length}/${measured.length}, medianMFE=${fmtPct(median(measured.map((r) => r.mfePct!)))}`;
}

async function main(): Promise<void> {
  const wallet = (process.argv[2] ?? process.env.TRACKED_WALLETS?.split(",")[0] ?? "").trim();
  const walletTxLimit = Number(process.argv[3] ?? "400");
  const mintLimit = Number(process.argv[4] ?? "50");
  const horizonMinutes = Number(process.argv[5] ?? "180");
  const lookbackMinutes = Number(process.argv[6] ?? "30");
  if (!wallet) throw new Error("Pass wallet arg or set TRACKED_WALLETS");
  if (!MORALIS_API_KEY) throw new Error("MORALIS_API_KEY is not set");

  console.log(
    `Source strategy lab wallet=${short(wallet)} txLimit=${walletTxLimit} ` +
      `mints=${mintLimit} horizon=${horizonMinutes}m lookback=${lookbackMinutes}m`
  );
  const walletTxs = await fetchPagedAddressTxs(wallet, walletTxLimit);
  const swaps = walletTxs
    .map((tx) => extractSwap(tx, wallet))
    .filter((swap): swap is Swap => swap != null)
    .sort((a, b) => a.timestamp - b.timestamp);
  const campaigns = buildCampaigns(swaps).slice(0, mintLimit);
  const rows: LabRow[] = [];

  for (const campaign of campaigns) {
    try {
      const pair = await getTokenPairInfo(campaign.mint);
      if (!pair?.pairAddress) throw new Error("no pair found");
      const from = Math.max(0, campaign.firstBuy.timestamp - lookbackMinutes * 60);
      const to = campaign.firstBuy.timestamp + horizonMinutes * 60;
      const candles = await fetchMoralisOhlcv(pair.pairAddress, from, to);
      const metrics = analyzeCandles(campaign, candles);
      rows.push({
        mint: campaign.mint,
        symbol: pair.baseToken.symbol || short(campaign.mint),
        sourceBuySol: sol(campaign.spentLamports),
        currentLiquidityUsd: pair.liquidityUsd,
        currentMarketCapUsd: pair.marketCapUsd,
        candles,
        ...metrics,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Skipping ${short(campaign.mint)}: ${msg}`);
    }
    await sleep(250);
  }

  const measured = rows.filter((r) => r.mfePct != null);
  const hit30 = measured.filter((r) => r.hit30);
  const timeTo30 = measured.map((r) => r.timeTo30Sec).filter((v): v is number => v != null);
  const ddBefore30 = measured.map((r) => r.drawdownBefore30Pct).filter((v): v is number => v != null);
  const sourceExitedBeforeAth = measured.filter((r) => r.sourceExitedBeforeAth === true);

  console.log("");
  console.log("1) MFE / MAE");
  console.log(`  Measured: ${measured.length}/${campaigns.length}`);
  console.log(`  Hit +30%: ${hit30.length}/${measured.length} (${((hit30.length / Math.max(1, measured.length)) * 100).toFixed(1)}%)`);
  console.log(`  Median MFE: ${fmtPct(median(measured.map((r) => r.mfePct!)))}`);
  console.log(`  Median MAE: ${fmtPct(median(measured.map((r) => r.maePct!)))}`);
  console.log(`  Median drawdown before +30: ${fmtPct(median(ddBefore30))}`);

  console.log("");
  console.log("2) Time to +30%");
  console.log(`  Median time to +30: ${median(timeTo30) == null ? "-" : `${Math.round(median(timeTo30)!)}s`}`);
  console.log(`  Avg time to +30: ${timeTo30.length ? `${Math.round(avg(timeTo30))}s` : "-"}`);

  console.log("");
  console.log("3) TP/SL Grid");
  const fixedCombos = [
    [20, 20],
    [25, 25],
    [30, 25],
    [30, 35],
    [40, 30],
    [50, 35],
  ];
  const sims = fixedCombos.map(([tp, sl]) =>
    summarizeSim(`TP${tp}/SL${sl}`, measured.map((r) => simulateFixed(r, tp!, sl!)))
  );
  sims.push(
    summarizeSim(
      "Trail SL30 steps 30:10,60:30,100:50",
      measured.map((r) => simulateTrailing(r, 30, [
        { peak: 30, floor: 10 },
        { peak: 60, floor: 30 },
        { peak: 100, floor: 50 },
      ]))
    )
  );
  sims.sort((a, b) => b.expectancyPct - a.expectancyPct);
  for (const sim of sims) {
    console.log(
      `  ${sim.name}: avg=${fmtPct(sim.avgPct)}, median=${fmtPct(sim.medianPct)}, ` +
        `winRate=${((sim.wins / Math.max(1, sim.trades)) * 100).toFixed(1)}%, trades=${sim.trades}`
    );
  }

  console.log("");
  console.log("4) Entry Filters");
  console.log(`  ${bucketStats(measured, "current liquidity >= $10k", (r) => (r.currentLiquidityUsd ?? 0) >= 10_000)}`);
  console.log(`  ${bucketStats(measured, "current liquidity < $10k", (r) => (r.currentLiquidityUsd ?? 0) < 10_000)}`);
  console.log(`  ${bucketStats(measured, "pre5m momentum >= +10%", (r) => (r.pre5mMomentumPct ?? -Infinity) >= 10)}`);
  console.log(`  ${bucketStats(measured, "pre5m momentum < +10%", (r) => (r.pre5mMomentumPct ?? -Infinity) < 10)}`);
  console.log(`  ${bucketStats(measured, "pre5m volume >= $5k", (r) => r.pre5mVolumeUsd >= 5_000)}`);
  console.log(`  ${bucketStats(measured, "pre5m volume < $5k", (r) => r.pre5mVolumeUsd < 5_000)}`);
  console.log(`  ${bucketStats(measured, "source buy >= 0.005 SOL", (r) => r.sourceBuySol >= 0.005)}`);
  console.log(`  ${bucketStats(measured, "source buy < 0.005 SOL", (r) => r.sourceBuySol < 0.005)}`);

  console.log("");
  console.log("5) Source Exit Quality");
  console.log(`  Source exits captured: ${measured.filter((r) => r.sourceExitPct != null).length}`);
  console.log(`  Median source exit: ${fmtPct(median(measured.map((r) => r.sourceExitPct).filter((v): v is number => v != null)))}`);
  console.log(`  Source exited before ATH: ${sourceExitedBeforeAth.length}/${measured.filter((r) => r.sourceExitedBeforeAth != null).length}`);
  console.log(`  Median ATH after source exit: ${fmtPct(median(measured.map((r) => r.athAfterSourceExitPct).filter((v): v is number => v != null)))}`);

  console.log("");
  console.log("Rows");
  for (const r of measured) {
    console.log(
      [
        short(r.mint),
        r.symbol,
        `buy=${r.sourceBuySol.toFixed(4)} SOL`,
        `MFE=${fmtPct(r.mfePct)}`,
        `MAE=${fmtPct(r.maePct)}`,
        `t30=${r.timeTo30Sec == null ? "-" : `${r.timeTo30Sec}s`}`,
        `ddTo30=${fmtPct(r.drawdownBefore30Pct)}`,
        `srcExit=${fmtPct(r.sourceExitPct)}`,
        `liq=${r.currentLiquidityUsd == null ? "-" : `$${Math.round(r.currentLiquidityUsd)}`}`,
        `mom5=${fmtPct(r.pre5mMomentumPct)}`,
        `vol5=$${Math.round(r.pre5mVolumeUsd)}`,
      ].join(" | ")
    );
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
