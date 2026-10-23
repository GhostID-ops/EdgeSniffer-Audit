#!/usr/bin/env node
import fs from "node:fs";

const WSOL = "So11111111111111111111111111111111111111112";
const MORALIS_BASE = "https://solana-gateway.moralis.io";
const MORALIS_API_KEY = process.env.MORALIS_API_KEY ?? "";
const TARGET = process.argv[2] ?? process.env.TRACKED_WALLETS?.split(",")[0]?.trim();
const START_ISO = process.argv[3] ?? "2026-07-18T00:00:00Z";
const END_ISO = process.argv[4] ?? "2026-07-19T00:00:00Z";
const HORIZON_HOURS = Number(process.argv[5] ?? "6");

if (!TARGET) throw new Error("usage: node scripts/analyzeTrackedWalletYesterdayAth.mjs WALLET [startIso] [endIso]");

const START = Date.parse(START_ISO);
const END = Date.parse(END_ISO);
const NOW = Math.floor(Date.now() / 1000);
const OUT = `data/${TARGET.slice(0, 6)}-ath-after-cashout-${START_ISO.slice(0, 10)}-${new Date()
  .toISOString()
  .replace(/[:.]/g, "-")}.md`;

function short(s) {
  return s ? `${s.slice(0, 6)}...${s.slice(-4)}` : "-";
}

function pct(a, b) {
  return a > 0 && Number.isFinite(a) && Number.isFinite(b) ? (b / a - 1) * 100 : null;
}

function fmtPct(x) {
  return x == null || !Number.isFinite(x) ? "-" : `${x >= 0 ? "+" : ""}${x.toFixed(1)}%`;
}

function fmtUsd(x) {
  return x == null || !Number.isFinite(x) ? "-" : `$${Math.round(x).toLocaleString()}`;
}

function fmtSol(x) {
  return x == null || !Number.isFinite(x) ? "-" : `${x.toFixed(4)} SOL`;
}

function iso(msOrSec) {
  const ms = msOrSec > 1e12 ? msOrSec : msOrSec * 1000;
  return new Date(ms).toISOString().replace("T", " ").slice(0, 16);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(url) {
  const response = await fetch(url, {
    signal: AbortSignal.timeout(15_000),
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`${response.status} ${text.slice(0, 120)}`);
  }
  return await response.json();
}

async function bestPair(mint) {
  const json = await fetchJson(`https://api.dexscreener.com/latest/dex/tokens/${mint}`);
  const pairs = (json.pairs || []).filter((pair) => pair.chainId === "solana" && pair.pairAddress && pair.priceUsd);
  pairs.sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0));
  return pairs[0] || null;
}

async function fetchCandles(pool, fromTs, toTs) {
  if (MORALIS_API_KEY) {
    try {
      const rows = await fetchMoralisCandles(pool, fromTs, toTs);
      if (rows.length > 0) return { provider: "moralis", rows };
    } catch (error) {
      console.warn(`Moralis OHLCV failed for ${short(pool)}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  const out = [];
  let before = toTs;
  for (let i = 0; i < 4; i += 1) {
    const url =
      `https://api.geckoterminal.com/api/v2/networks/solana/pools/${pool}/ohlcv/minute` +
      `?aggregate=1&limit=1000&currency=usd&before_timestamp=${before}`;
    const json = await fetchJson(url);
    const list = json?.data?.attributes?.ohlcv_list || [];
    if (list.length === 0) break;
    for (const candle of list) {
      const [ts, open, high, low, close, volume] = candle;
      if (ts >= fromTs && ts <= toTs) {
        out.push({ ts, open: +open, high: +high, low: +low, close: +close, volume: +volume });
      }
    }
    const minTs = Math.min(...list.map((candle) => candle[0]));
    if (minTs <= fromTs) break;
    before = minTs - 1;
    await sleep(250);
  }
  const byTs = new Map();
  for (const candle of out) byTs.set(candle.ts, candle);
  return { provider: "gecko", rows: [...byTs.values()].sort((a, b) => a.ts - b.ts) };
}

async function fetchMoralisCandles(pool, fromTs, toTs) {
  const span = Math.max(0, toTs - fromTs);
  const timeframe = span <= 6 * 60 * 60 ? "1min" : span <= 3 * 24 * 60 * 60 ? "5min" : "1h";
  const rows = [];
  let cursor;
  for (let page = 0; page < 20; page += 1) {
    const params = new URLSearchParams({
      fromDate: String(fromTs),
      toDate: String(toTs),
      timeframe,
      currency: "usd",
      limit: "1000",
    });
    if (cursor) params.set("cursor", cursor);
    const response = await fetch(`${MORALIS_BASE}/token/mainnet/pairs/${pool}/ohlcv?${params.toString()}`, {
      headers: { "X-Api-Key": MORALIS_API_KEY },
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) {
      const text = await response.text().catch(() => "");
      throw new Error(`Moralis ${response.status}: ${text.slice(0, 180)}`);
    }
    const json = await response.json();
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
  return rows.sort((a, b) => a.ts - b.ts);
}

function candleAtOrAfter(candles, ts) {
  return candles.find((candle) => candle.ts >= ts) || candles.at(-1) || null;
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  return xs.length ? xs[Math.floor(xs.length / 2)] : null;
}

const trades = fs
  .readFileSync("data/trades.jsonl", "utf8")
  .trim()
  .split(/\n+/)
  .map((line) => JSON.parse(line));

const copiedBuys = trades.filter(
  (row) =>
    row.ts >= START &&
    row.ts < END &&
    row.side === "buy" &&
    row.sourceWallet === TARGET &&
    row.inputMint === WSOL
);
const mints = [...new Set(copiedBuys.map((row) => row.outputMint))];
const rows = [];

for (const mint of mints) {
  const mintBuys = copiedBuys.filter((row) => row.outputMint === mint).sort((a, b) => a.ts - b.ts);
  const firstBuy = mintBuys[0];
  const firstBuyTs = Math.floor(firstBuy.ts / 1000);
  const allSells = trades.filter((row) => row.side === "sell" && row.inputMint === mint && row.ts >= firstBuy.ts).sort((a, b) => a.ts - b.ts);
  const sameWindowSells = allSells.filter((row) => row.ts < END);
  const finalSell = sameWindowSells.at(-1) || allSells.at(-1) || null;
  const finalSellTs = finalSell ? Math.floor(finalSell.ts / 1000) : null;
  const spent = mintBuys.reduce((sum, row) => sum + (row.inputAmountRaw || 0), 0) / 1e9;
  const received = sameWindowSells.reduce((sum, row) => sum + (row.outputAmountRaw || 0), 0) / 1e9;

  let row = {
    mint,
    symbol: firstBuy.outputSymbol || short(mint),
    buyAt: firstBuyTs,
    buys: mintBuys.length,
    sells: sameWindowSells.length,
    spent,
    received,
    realizedPct: sameWindowSells.length ? pct(spent, received) : null,
    finalSellTs,
  };

  try {
    const pair = await bestPair(mint);
    if (!pair) throw new Error("no DexScreener pair");
    const to = Math.min(NOW, firstBuyTs + HORIZON_HOURS * 3600);
    const fetched = await fetchCandles(pair.pairAddress, Math.max(0, firstBuyTs - 300), to);
    const candles = fetched.rows;
    const entryCandle = candleAtOrAfter(candles, firstBuyTs);
    const entry = entryCandle?.open || entryCandle?.close || null;
    const post = entry ? candles.filter((candle) => candle.ts >= firstBuyTs) : [];
    const peak = post.reduce((best, candle) => (!best || candle.high > best.high ? candle : best), null);
    const afterSell = finalSellTs && entry ? candles.filter((candle) => candle.ts >= finalSellTs) : [];
    const peakAfterSell = afterSell.reduce((best, candle) => (!best || candle.high > best.high ? candle : best), null);

    row = {
      ...row,
      pair: pair.pairAddress,
      dex: pair.dexId,
      entryMc: entry ? entry * 1_000_000_000 : null,
      currentMc: typeof (pair.marketCap ?? pair.fdv) === "number" ? pair.marketCap ?? pair.fdv : null,
      athPct: entry && peak ? pct(entry, peak.high) : null,
      athAt: peak?.ts || null,
      afterSellAthPct: entry && peakAfterSell ? pct(entry, peakAfterSell.high) : null,
      afterSellAthAt: peakAfterSell?.ts || null,
      leftAfterCashoutPct:
        entry && peakAfterSell && sameWindowSells.length ? pct(1 + (row.realizedPct ?? 0) / 100, 1 + pct(entry, peakAfterSell.high) / 100) : null,
      hit30: entry ? post.some((candle) => pct(entry, candle.high) >= 30) : false,
      hit50: entry ? post.some((candle) => pct(entry, candle.high) >= 50) : false,
      candles: post.length,
      provider: fetched.provider,
    };
  } catch (error) {
    row = { ...row, error: error instanceof Error ? error.message : String(error) };
  }
  rows.push(row);
  console.log(`${rows.length}/${mints.length} ${short(mint)} ${row.error || fmtPct(row.athPct)}`);
  await sleep(500);
}

const measured = rows.filter((row) => row.athPct != null);
const afterSellMeasured = measured.filter((row) => row.afterSellAthPct != null && row.sells > 0);
const lines = [];
lines.push(`# ${short(TARGET)} Token ATH After Our Cashout`);
lines.push("");
lines.push(`Generated: ${new Date().toISOString()}`);
lines.push(`Window: ${START_ISO} to ${END_ISO}`);
lines.push(`ATH horizon after each buy: ${HORIZON_HOURS}h`);
lines.push("");
lines.push("## Summary");
lines.push(`- Tokens copied: ${rows.length}`);
lines.push(`- Candle-measured: ${measured.length}/${rows.length}`);
lines.push(`- Hit +30% from our entry: ${measured.filter((row) => row.hit30).length}/${measured.length}`);
lines.push(`- Hit +50% from our entry: ${measured.filter((row) => row.hit50).length}/${measured.length}`);
lines.push(`- Median full-session ATH/MFE: ${fmtPct(median(measured.map((row) => row.athPct)))}`);
lines.push(`- Median ATH after our final cashout: ${fmtPct(median(afterSellMeasured.map((row) => row.afterSellAthPct)))}`);
lines.push("");
lines.push("## Rows");
lines.push(
  "| Token | Mint | Buy UTC | Spent | Sells | Realized | Entry MC | ATH From Entry | ATH Time | ATH After Cashout | Final Sell UTC | Hit 30 | Hit 50 | Notes |"
);
lines.push("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|");
for (const row of rows) {
  lines.push(
    `| ${row.symbol || short(row.mint)} | \`${short(row.mint)}\` | ${iso(row.buyAt)} | ${fmtSol(row.spent)} | ${row.sells} | ${fmtPct(
      row.realizedPct
    )} | ${fmtUsd(row.entryMc)} | ${fmtPct(row.athPct)} | ${row.athAt ? iso(row.athAt) : "-"} | ${fmtPct(
      row.afterSellAthPct
    )} | ${row.finalSellTs ? iso(row.finalSellTs) : "-"} | ${row.hit30 ? "yes" : "no"} | ${row.hit50 ? "yes" : "no"} | ${
      row.error || `${row.provider}: ${row.candles} candles`
    } |`
  );
}
lines.push("");
lines.push("## Read");
lines.push("- ATH/MFE uses Moralis OHLCV when `MORALIS_API_KEY` is present; GeckoTerminal is only a fallback.");
lines.push("- Entry MC is approximate: entry USD price * 1B pump.fun supply.");
lines.push("- Realized is same-window trade-log cashflow for sells before the end timestamp.");
lines.push("- ATH after cashout shows how much was still available after our last sell for that mint.");

fs.mkdirSync("data", { recursive: true });
fs.writeFileSync(OUT, lines.join("\n"));
fs.writeFileSync(OUT.replace(/\.md$/, ".json"), JSON.stringify({ generatedAt: new Date().toISOString(), target: TARGET, start: START_ISO, end: END_ISO, rows }, null, 2));

console.log("");
console.log(`REPORT ${OUT}`);
console.log(lines.slice(0, 80).join("\n"));
