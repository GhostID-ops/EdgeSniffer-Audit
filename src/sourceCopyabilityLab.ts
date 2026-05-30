import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { heliusAddressTransactionsUrl } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const MORALIS_BASE = "https://solana-gateway.moralis.io";
const MORALIS_API_KEY = process.env.MORALIS_API_KEY ?? "";
const CACHE_DIR = path.resolve(process.cwd(), "data", "copyability-lab-cache");

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

type Position = {
  mint: string;
  heldRaw: number;
  costLamports: number;
  realizedLamports: number;
  boughtLamports: number;
  soldLamports: number;
  buyCount: number;
  sellCount: number;
  hiddenSellLamports: number;
};

type Entry = {
  mint: string;
  timestamp: number;
  signature: string;
  solLamports: number;
  tokenRaw: number;
};

type Candle = {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

type EntryLab = {
  entry: Entry;
  symbol: string;
  candles: Candle[];
  entryPrice: number | null;
  mfePct: number | null;
  maePct: number | null;
  hit30: boolean;
  timeTo30Sec: number | null;
  pre5mVolumeUsd: number;
  pre5mMomentumPct: number | null;
};

type SimResult = {
  name: string;
  trades: number;
  wins: number;
  losses: number;
  avgPct: number;
  medianPct: number | null;
  totalPct: number;
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
  return `${sol(lamports).toFixed(4)} SOL`;
}

function fmtPct(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function avg(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function median(values: number[]): number | null {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  return sorted[Math.floor(sorted.length / 2)] ?? null;
}

function pct(from: number, to: number): number {
  return ((to - from) / from) * 100;
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

async function fetchWalletTxsUntil(address: string, cutoffTs: number, maxTxs: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(address, Math.min(100, maxTxs - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    const oldest = batch[batch.length - 1]?.timestamp ?? 0;
    if (oldest > 0 && oldest < cutoffTs) break;
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
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

function positionFor(positions: Map<string, Position>, mint: string): Position {
  let pos = positions.get(mint);
  if (!pos) {
    pos = {
      mint,
      heldRaw: 0,
      costLamports: 0,
      realizedLamports: 0,
      boughtLamports: 0,
      soldLamports: 0,
      buyCount: 0,
      sellCount: 0,
      hiddenSellLamports: 0,
    };
    positions.set(mint, pos);
  }
  return pos;
}

function applySwap(pos: Position, swap: Swap, countRealized: boolean): number | null {
  if (swap.side === "buy") {
    pos.heldRaw += swap.tokenRaw;
    pos.costLamports += swap.solLamports;
    if (countRealized) {
      pos.boughtLamports += swap.solLamports;
      pos.buyCount++;
    }
    return null;
  }

  const sellRaw = Math.min(pos.heldRaw, swap.tokenRaw);
  if (sellRaw <= 0 || pos.heldRaw <= 0) {
    if (countRealized) {
      pos.hiddenSellLamports += swap.solLamports;
      pos.soldLamports += swap.solLamports;
      pos.sellCount++;
    }
    return null;
  }

  const costBasis = pos.costLamports * (sellRaw / pos.heldRaw);
  const proceeds = swap.solLamports * (sellRaw / swap.tokenRaw);
  const pnl = proceeds - costBasis;
  pos.heldRaw -= sellRaw;
  pos.costLamports -= costBasis;
  if (countRealized) {
    pos.realizedLamports += pnl;
    pos.soldLamports += proceeds;
    pos.sellCount++;
  }
  return countRealized ? pnl : null;
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
    const response = await fetch(`${MORALIS_BASE}/token/mainnet/pairs/${poolAddress}/ohlcv?${params}`, {
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
      rows.push({ ts, open: item.open, high: item.high, low: item.low, close: item.close, volume: item.volume ?? 0 });
    }
    cursor = json.cursor ?? undefined;
    if (!cursor) break;
    await sleep(250);
  }
  const out = rows.filter((row) => row.ts >= fromTs && row.ts <= toTs).sort((a, b) => a.ts - b.ts);
  await cacheSet(cacheKey, out);
  return out;
}

function candleAtOrAfter(candles: Candle[], ts: number): Candle | null {
  return candles.find((c) => c.ts >= ts) ?? candles[candles.length - 1] ?? null;
}

function analyzeEntry(entry: Entry, symbol: string, candles: Candle[]): EntryLab {
  const entryCandle = candleAtOrAfter(candles, entry.timestamp);
  const entryPrice = entryCandle?.open ?? null;
  const post = entryPrice == null ? [] : candles.filter((c) => c.ts >= entry.timestamp);
  const pre5 = entryPrice == null ? [] : candles.filter((c) => c.ts >= entry.timestamp - 300 && c.ts < entry.timestamp);
  const pre5mVolumeUsd = pre5.reduce((sum, candle) => sum + candle.volume, 0);
  const pre5mMomentumPct = pre5.length > 0 && entryPrice != null ? pct(pre5[0]!.open, entryPrice) : null;
  if (entryPrice == null || post.length === 0) {
    return { entry, symbol, candles, entryPrice, mfePct: null, maePct: null, hit30: false, timeTo30Sec: null, pre5mVolumeUsd, pre5mMomentumPct };
  }
  const peak = post.reduce((best, c) => (c.high > best.high ? c : best), post[0]!);
  const trough = post.reduce((best, c) => (c.low < best.low ? c : best), post[0]!);
  const hit30 = post.find((c) => pct(entryPrice, c.high) >= 30);
  return {
    entry,
    symbol,
    candles,
    entryPrice,
    mfePct: pct(entryPrice, peak.high),
    maePct: pct(entryPrice, trough.low),
    hit30: Boolean(hit30),
    timeTo30Sec: hit30 ? hit30.ts - entry.timestamp : null,
    pre5mVolumeUsd,
    pre5mMomentumPct,
  };
}

function simulateFixed(row: EntryLab, tpPct: number, slPct: number): number | null {
  if (row.entryPrice == null) return null;
  const post = row.candles.filter((c) => c.ts >= row.entry.timestamp);
  for (const c of post) {
    const low = pct(row.entryPrice, c.low);
    const high = pct(row.entryPrice, c.high);
    if (low <= -slPct && high >= tpPct) return -slPct;
    if (low <= -slPct) return -slPct;
    if (high >= tpPct) return tpPct;
  }
  const last = post[post.length - 1];
  return last ? pct(row.entryPrice, last.close) : null;
}

function summarizeSim(name: string, values: Array<number | null>): SimResult {
  const xs = values.filter((value): value is number => value != null && Number.isFinite(value));
  return {
    name,
    trades: xs.length,
    wins: xs.filter((v) => v > 0).length,
    losses: xs.filter((v) => v < 0).length,
    avgPct: avg(xs),
    medianPct: median(xs),
    totalPct: xs.reduce((sum, v) => sum + v, 0),
  };
}

const TP_SL_COMBOS = [
  [10, 15],
  [15, 15],
  [20, 20],
  [25, 25],
  [30, 25],
  [30, 35],
  [40, 30],
  [50, 35],
] as const;

function printSimGrid(title: string, rows: EntryLab[]): void {
  const sims = TP_SL_COMBOS.map(([tp, sl]) =>
    summarizeSim(`TP${tp}/SL${sl}`, rows.map((row) => simulateFixed(row, tp, sl)))
  );
  sims.sort((a, b) => b.avgPct - a.avgPct);
  console.log("");
  console.log(title);
  for (const sim of sims) {
    console.log(
      `  ${sim.name}: avg=${fmtPct(sim.avgPct)}, median=${fmtPct(sim.medianPct)}, ` +
        `winRate=${((sim.wins / Math.max(1, sim.trades)) * 100).toFixed(1)}%, trades=${sim.trades}`
    );
  }
}

async function main(): Promise<void> {
  const wallet = (process.argv[2] ?? process.env.TRACKED_WALLETS?.split(",")[0] ?? "").trim();
  const analysisHours = Number(process.argv[3] ?? "24");
  const warmupHours = Number(process.argv[4] ?? "72");
  const maxTxs = Number(process.argv[5] ?? "2500");
  const entryLimit = Number(process.argv[6] ?? "80");
  const horizonMinutes = Number(process.argv[7] ?? "180");
  if (!wallet) throw new Error("Pass wallet arg or set TRACKED_WALLETS");

  const nowTs = Math.floor(Date.now() / 1000);
  const sinceTs = nowTs - Math.round(analysisHours * 3600);
  const warmupTs = nowTs - Math.round(warmupHours * 3600);
  console.log(
    `Copyability lab wallet=${short(wallet)} window=${analysisHours}h warmup=${warmupHours}h ` +
      `maxTxs=${maxTxs} entries=${entryLimit}`
  );

  const txs = await fetchWalletTxsUntil(wallet, warmupTs, maxTxs);
  const swaps = txs
    .map((tx) => extractSwap(tx, wallet))
    .filter((swap): swap is Swap => swap != null)
    .sort((a, b) => a.timestamp - b.timestamp);

  const positions = new Map<string, Position>();
  const sellPnls: number[] = [];
  const sellWinPnls: number[] = [];
  const entries: Entry[] = [];
  for (const swap of swaps) {
    const pos = positionFor(positions, swap.mint);
    const inWindow = swap.timestamp >= sinceTs;
    const pnl = applySwap(pos, swap, inWindow);
    if (inWindow && swap.side === "buy") {
      entries.push({
        mint: swap.mint,
        timestamp: swap.timestamp,
        signature: swap.signature,
        solLamports: swap.solLamports,
        tokenRaw: swap.tokenRaw,
      });
    }
    if (pnl != null) {
      sellPnls.push(pnl);
      if (pnl > 0) sellWinPnls.push(pnl);
    }
  }

  const windowPositions = [...positions.values()];
  const realized = windowPositions.reduce((sum, p) => sum + p.realizedLamports, 0);
  const bought = windowPositions.reduce((sum, p) => sum + p.boughtLamports, 0);
  const sold = windowPositions.reduce((sum, p) => sum + p.soldLamports, 0);
  const hiddenSells = windowPositions.reduce((sum, p) => sum + p.hiddenSellLamports, 0);
  const buys = windowPositions.reduce((sum, p) => sum + p.buyCount, 0);
  const sells = windowPositions.reduce((sum, p) => sum + p.sellCount, 0);

  console.log("");
  console.log("Source Window Reconstruction");
  console.log(`  Swaps in fetched set: ${swaps.length}; buys in window: ${buys}; sells in window: ${sells}`);
  console.log(`  Approx realized PnL in window: ${fmtSol(realized)} (${bought > 0 ? fmtPct((realized / bought) * 100) : "-"})`);
  console.log(`  Bought in window: ${fmtSol(bought)}; sold proceeds in window: ${fmtSol(sold)}`);
  console.log(`  Sell-level win rate: ${sellPnls.filter((p) => p > 0).length}/${sellPnls.length}`);
  if (hiddenSells > 0) {
    console.log(`  Hidden/opening-inventory sell proceeds not PnL-attributed: ${fmtSol(hiddenSells)}`);
  }

  const labs: EntryLab[] = [];
  for (const entry of entries.sort((a, b) => b.timestamp - a.timestamp).slice(0, entryLimit)) {
    try {
      const pair = await getTokenPairInfo(entry.mint);
      if (!pair?.pairAddress) continue;
      const candles = await fetchMoralisOhlcv(
        pair.pairAddress,
        Math.max(0, entry.timestamp - 30 * 60),
        entry.timestamp + horizonMinutes * 60
      );
      labs.push(analyzeEntry(entry, pair.baseToken.symbol || short(entry.mint), candles));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Skipping entry ${short(entry.mint)}: ${msg}`);
    }
    await sleep(250);
  }

  const measured = labs.filter((row) => row.mfePct != null);
  const hit30 = measured.filter((row) => row.hit30);
  const times = measured.map((row) => row.timeTo30Sec).filter((v): v is number => v != null);
  console.log("");
  console.log("Copyability From Entries");
  console.log(`  Entries measured: ${measured.length}/${Math.min(entryLimit, entries.length)}`);
  console.log(`  Hit +30% within ${horizonMinutes}m: ${hit30.length}/${measured.length}`);
  console.log(`  Median MFE: ${fmtPct(median(measured.map((r) => r.mfePct!)))}`);
  console.log(`  Median MAE: ${fmtPct(median(measured.map((r) => r.maePct!)))}`);
  console.log(`  Median time to +30: ${times.length ? `${Math.round(median(times)!)}s` : "-"}`);

  printSimGrid("Bot-Style TP/SL Grid", measured);

  console.log("");
  console.log("Filters");
  const vol5 = measured.filter((r) => r.pre5mVolumeUsd >= 5_000);
  const lowVol = measured.filter((r) => r.pre5mVolumeUsd < 5_000);
  const vol5Momentum = vol5.filter((r) => (r.pre5mMomentumPct ?? -Infinity) > 0);
  console.log(`  pre5m volume >= $5k: n=${vol5.length}, hit30=${vol5.filter((r) => r.hit30).length}/${vol5.length}, medianMFE=${fmtPct(median(vol5.map((r) => r.mfePct!)))}`);
  console.log(`  pre5m volume < $5k: n=${lowVol.length}, hit30=${lowVol.filter((r) => r.hit30).length}/${lowVol.length}, medianMFE=${fmtPct(median(lowVol.map((r) => r.mfePct!)))}`);
  console.log(`  pre5m volume >= $5k and momentum > 0: n=${vol5Momentum.length}, hit30=${vol5Momentum.filter((r) => r.hit30).length}/${vol5Momentum.length}, medianMFE=${fmtPct(median(vol5Momentum.map((r) => r.mfePct!)))}`);

  printSimGrid("Filtered Grid: pre5m volume >= $5k", vol5);
  printSimGrid("Filtered Grid: pre5m volume >= $5k and momentum > 0", vol5Momentum);

  console.log("");
  console.log("Recent Measured Entries");
  for (const row of measured.slice(0, 30)) {
    console.log(
      [
        short(row.entry.mint),
        row.symbol,
        `buy=${sol(row.entry.solLamports).toFixed(3)} SOL`,
        `MFE=${fmtPct(row.mfePct)}`,
        `MAE=${fmtPct(row.maePct)}`,
        `t30=${row.timeTo30Sec == null ? "-" : `${row.timeTo30Sec}s`}`,
        `vol5=$${Math.round(row.pre5mVolumeUsd)}`,
        `mom5=${fmtPct(row.pre5mMomentumPct)}`,
      ].join(" | ")
    );
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
