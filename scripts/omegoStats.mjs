import fs from "node:fs";

const path =
  process.argv[2] ??
  "data/path-wallet-audit-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq.json";

const data = JSON.parse(fs.readFileSync(path, "utf8"));
const rows = data.rows.filter(
  (row) => Number.isFinite(row.mfePct) && Number.isFinite(row.maePct)
);

function avg(values) {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function pct(count, total) {
  return total > 0 ? (count / total) * 100 : 0;
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtUsd(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `$${Math.round(value).toLocaleString()}`;
}

function fmtNum(value, digits = 2) {
  if (value == null || !Number.isFinite(value)) return "-";
  return value.toFixed(digits);
}

function short(value) {
  if (!value) return "-";
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function bucket(name, predicate) {
  const xs = rows.filter(predicate);
  const n = xs.length;
  const hits = (threshold) => xs.filter((row) => row.mfePct >= threshold).length;
  const bad = (threshold) => xs.filter((row) => row.maePct <= -threshold).length;
  const closed = xs.filter((row) => row.sourceExitPct != null);
  const twoXMedian = median(xs.map((row) => row.timeTo2xSec ?? NaN));
  return {
    name,
    n,
    medMc: fmtUsd(median(xs.map((row) => row.entryMarketCapUsdApprox))),
    medBuySol: fmtNum(median(xs.map((row) => row.sourceBuySol)), 3),
    hit30: fmtPct(pct(hits(30), n)),
    hit50: fmtPct(pct(hits(50), n)),
    hit100: fmtPct(pct(hits(100), n)),
    hit200: fmtPct(pct(hits(200), n)),
    medMfe: fmtPct(median(xs.map((row) => row.mfePct))),
    avgMfe: fmtPct(avg(xs.map((row) => row.mfePct))),
    medMae: fmtPct(median(xs.map((row) => row.maePct))),
    mae25: fmtPct(pct(bad(25), n)),
    mae50: fmtPct(pct(bad(50), n)),
    sourceWin: fmtPct(pct(closed.filter((row) => row.sourceExitPct > 0).length, closed.length)),
    medSourceExit: fmtPct(median(closed.map((row) => row.sourceExitPct))),
    twoXCount: xs.filter((row) => row.timeTo2xSec != null).length,
    med2xTime: twoXMedian == null ? "-" : `${Math.round(twoXMedian / 60)}m`,
  };
}

function pctMove(from, to) {
  return ((to - from) / from) * 100;
}

function fixedExit(row, takeProfit, stopLoss) {
  const entry = row.candles[0]?.open;
  if (!entry) return 0;
  for (const candle of row.candles) {
    const low = pctMove(entry, candle.low);
    const high = pctMove(entry, candle.high);
    if (low <= -stopLoss) return -stopLoss;
    if (high >= takeProfit) return takeProfit;
  }
  return pctMove(entry, row.candles.at(-1).close);
}

function trailExit(row, arm, giveback, emergencyStop) {
  const entry = row.candles[0]?.open;
  if (!entry) return 0;
  let peakPct = 0;
  for (const candle of row.candles) {
    const low = pctMove(entry, candle.low);
    const high = pctMove(entry, candle.high);
    peakPct = Math.max(peakPct, high);
    if (low <= -emergencyStop) return -emergencyStop;
    if (peakPct >= arm && low <= peakPct - giveback) return peakPct - giveback;
  }
  return pctMove(entry, row.candles.at(-1).close);
}

function summarizeReturns(values) {
  const wins = values.filter((value) => value > 0);
  const losses = values.filter((value) => value < 0);
  const profit = wins.reduce((sum, value) => sum + value, 0);
  const loss = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  return {
    avg: fmtPct(avg(values)),
    median: fmtPct(median(values)),
    winRate: fmtPct(pct(wins.length, values.length)),
    profitFactor: loss > 0 ? fmtNum(profit / loss, 2) : profit > 0 ? "inf" : "0.00",
  };
}

function strategyBucket(name, predicate) {
  const xs = rows.filter(predicate);
  const source = xs.map((row) => row.sourceExitPct).filter(Number.isFinite);
  return {
    name,
    n: xs.length,
    source: summarizeReturns(source),
    tp30sl25: summarizeReturns(xs.map((row) => fixedExit(row, 30, 25))),
    tp50sl30: summarizeReturns(xs.map((row) => fixedExit(row, 50, 30))),
    trail45give30sl35: summarizeReturns(xs.map((row) => trailExit(row, 45, 30, 35))),
  };
}

const buckets = [
  bucket("All", () => true),
  bucket("MC < 5k", (row) => (row.entryMarketCapUsdApprox ?? Infinity) < 5_000),
  bucket("MC 5k-8k", (row) => {
    const mc = row.entryMarketCapUsdApprox;
    return mc >= 5_000 && mc < 8_000;
  }),
  bucket("MC 8k-12k", (row) => {
    const mc = row.entryMarketCapUsdApprox;
    return mc >= 8_000 && mc < 12_000;
  }),
  bucket("MC 12k-20k", (row) => {
    const mc = row.entryMarketCapUsdApprox;
    return mc >= 12_000 && mc < 20_000;
  }),
  bucket("MC >= 20k", (row) => (row.entryMarketCapUsdApprox ?? -Infinity) >= 20_000),
  bucket("Earlier buys <= 5", (row) => (row.lead?.earlierBuys ?? 999) <= 5),
  bucket("Earlier buys > 40", (row) => (row.lead?.earlierBuys ?? 0) > 40),
  bucket("Source order <= 10", (row) => (row.lead?.sourceOrder ?? 999) <= 10),
  bucket("Source order > 50", (row) => (row.lead?.sourceOrder ?? 0) > 50),
];

const strategyBuckets = [
  strategyBucket("All", () => true),
  strategyBucket("MC < 5k", (row) => (row.entryMarketCapUsdApprox ?? Infinity) < 5_000),
  strategyBucket("MC 5k-8k", (row) => {
    const mc = row.entryMarketCapUsdApprox;
    return mc >= 5_000 && mc < 8_000;
  }),
  strategyBucket("MC 8k-12k", (row) => {
    const mc = row.entryMarketCapUsdApprox;
    return mc >= 8_000 && mc < 12_000;
  }),
  strategyBucket("MC 12k-20k", (row) => {
    const mc = row.entryMarketCapUsdApprox;
    return mc >= 12_000 && mc < 20_000;
  }),
  strategyBucket("MC >= 20k", (row) => (row.entryMarketCapUsdApprox ?? -Infinity) >= 20_000),
  strategyBucket("Earlier buys <= 5", (row) => (row.lead?.earlierBuys ?? 999) <= 5),
  strategyBucket("Earlier buys > 40", (row) => (row.lead?.earlierBuys ?? 0) > 40),
];

const topAth = [...rows]
  .sort((a, b) => b.mfePct - a.mfePct)
  .slice(0, 15)
  .map((row) => ({
    token: row.symbol,
    mint: short(row.mint),
    buySol: fmtNum(row.sourceBuySol, 3),
    entryMc: fmtUsd(row.entryMarketCapUsdApprox),
    mfe: fmtPct(row.mfePct),
    mae: fmtPct(row.maePct),
    sourceExit: fmtPct(row.sourceExitPct),
    twoXTime: row.timeTo2xSec == null ? "-" : `${Math.round(row.timeTo2xSec / 60)}m`,
    earlierBuys: row.lead?.earlierBuys ?? null,
  }));

const ruggy = [...rows]
  .sort((a, b) => a.maePct - b.maePct)
  .slice(0, 15)
  .map((row) => ({
    token: row.symbol,
    mint: short(row.mint),
    buySol: fmtNum(row.sourceBuySol, 3),
    entryMc: fmtUsd(row.entryMarketCapUsdApprox),
    mfe: fmtPct(row.mfePct),
    mae: fmtPct(row.maePct),
    sourceExit: fmtPct(row.sourceExitPct),
    earlierBuys: row.lead?.earlierBuys ?? null,
  }));

console.log(JSON.stringify({
  sample: data.sample,
  performance: data.performance,
  pathStats: data.pathStats,
  strategies: data.strategies,
  buckets,
  strategyBuckets,
  topAth,
  ruggy,
}, null, 2));
