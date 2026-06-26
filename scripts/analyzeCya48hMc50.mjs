import "dotenv/config";
import fs from "node:fs/promises";
import path from "node:path";

const WALLET = process.argv[2] ?? "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o";
const HOURS = Number(process.argv[3] ?? "48");
const MIN_ENTRY_MC_USD = Number(process.argv[4] ?? "50000");
const HORIZON_HOURS = Number(process.argv[5] ?? "24");
const ROUTE_FILTER = String(process.argv[6] ?? process.env.CYA_ANALYSIS_ROUTE_FILTER ?? "all").toLowerCase();
const WINDOW_START_HOURS_AGO = Number(process.argv[7] ?? process.env.CYA_ANALYSIS_WINDOW_START_HOURS_AGO ?? "0");
const HELIUS_API_KEY = process.env.HELIUS_API_KEY ?? "";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const GECKO_BASE = "https://api.geckoterminal.com/api/v2";
const SUPPLY_APPROX = Number(process.env.PREBUY_INFERRED_TOKEN_SUPPLY ?? "1000000000");
const SOL_USD = Number(process.env.LOCAL_PUMPFUN_SOL_PRICE_USD ?? process.env.SOL_PRICE_USD ?? "150");
const TARGETS = [30, 50, 75, 100];
const TRAILING_STEPS = parseTrailingSteps(
  process.env.CYA_ANALYSIS_TRAILING_STEPS ?? "50:25,100:75,200:125,300:200,500:350,600:400,800:500,1200:1000"
);
const CACHE_DIR = path.resolve(process.cwd(), "data", "cya-48h-mc50-cache");
const OUT_TAG =
  [
    "cya-48h-mc50-analysis",
    ROUTE_FILTER === "all" ? "" : ROUTE_FILTER.replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""),
    WINDOW_START_HOURS_AGO > 0 ? `${WINDOW_START_HOURS_AGO}h-to-${WINDOW_START_HOURS_AGO + HOURS}h-ago` : "",
  ]
    .filter(Boolean)
    .join("-");
const OUT_JSON = path.resolve(process.cwd(), "data", `${OUT_TAG}.json`);
const OUT_MD = path.resolve(process.cwd(), "data", `${OUT_TAG}.md`);
const PAGE_SLEEP_MS = Number(process.env.CYA_ANALYSIS_PAGE_SLEEP_MS ?? "250");
const CANDIDATE_SLEEP_MS = Number(process.env.CYA_ANALYSIS_CANDIDATE_SLEEP_MS ?? "700");
const RATE_LIMIT_SLEEP_MS = Number(process.env.CYA_ANALYSIS_429_SLEEP_MS ?? "1500");

if (!HELIUS_API_KEY) {
  console.error("HELIUS_API_KEY is not set in .env.");
  process.exit(1);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(addr) {
  return addr ? `${addr.slice(0, 6)}...${addr.slice(-4)}` : "-";
}

function sol(lamports) {
  return lamports / 1_000_000_000;
}

function avg(values) {
  const xs = values.filter(Number.isFinite);
  return xs.length ? xs.reduce((sum, value) => sum + value, 0) / xs.length : null;
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function quantile(values, q) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  return xs[Math.min(xs.length - 1, Math.max(0, Math.floor((xs.length - 1) * q)))];
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtUsd(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

function fmtSol(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value.toFixed(3)} SOL`;
}

function isPumpFunOrPumpSwapMint(mint) {
  // PumpSwap graduations keep the original pump.fun mint, whose public key uses
  // the pump.fun vanity suffix. This excludes BAGS/Raydium/Meteora/etc.
  return String(mint ?? "").endsWith("pump");
}

function matchesRouteFilter(campaign) {
  if (ROUTE_FILTER === "all") return true;
  if (["pump", "pumpfun", "pumpswap", "pumpfun-pumpswap", "pumpfun_pumpswap"].includes(ROUTE_FILTER)) {
    return isPumpFunOrPumpSwapMint(campaign.mint);
  }
  throw new Error(
    `Unsupported route filter "${ROUTE_FILTER}". Use "all" or "pumpfun-pumpswap".`
  );
}

function parseTrailingSteps(raw) {
  return String(raw ?? "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [peak, floor] = part.split(":").map(Number);
      return Number.isFinite(peak) && Number.isFinite(floor) ? { peak, floor } : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.peak - b.peak);
}

async function readCache(key) {
  try {
    return JSON.parse(await fs.readFile(path.join(CACHE_DIR, key), "utf8"));
  } catch {
    return null;
  }
}

async function writeCache(key, value) {
  await fs.mkdir(CACHE_DIR, { recursive: true });
  await fs.writeFile(path.join(CACHE_DIR, key), JSON.stringify(value), "utf8");
}

async function fetchJson(url, options = {}) {
  let lastErr;
  for (let attempt = 1; attempt <= 4; attempt++) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(options.timeoutMs ?? 15_000) });
      if (res.status === 429 || res.status >= 500) {
        const text = await res.text();
        lastErr = new Error(`HTTP ${res.status}: ${text.slice(0, 160)}`);
        await sleep(attempt * RATE_LIMIT_SLEEP_MS);
        continue;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 160)}`);
      return await res.json();
    } catch (err) {
      lastErr = err;
      await sleep(attempt * 1000);
    }
  }
  throw lastErr;
}

async function fetchWalletTxsSince(wallet, cutoffTs) {
  const all = [];
  let before = null;
  for (let page = 0; page < 80; page++) {
    const url =
      `https://api.helius.xyz/v0/addresses/${wallet}/transactions?api-key=${HELIUS_API_KEY}` +
      `&limit=100${before ? `&before=${before}` : ""}`;
    const batch = await fetchJson(url, { timeoutMs: 20_000 });
    if (!Array.isArray(batch) || batch.length === 0) break;
    all.push(...batch);
    before = batch.at(-1)?.signature;
    const oldest = Math.min(...batch.map((tx) => tx.timestamp ?? Infinity));
    console.log(`Helius page ${page + 1}: total=${all.length}, oldest=${new Date(oldest * 1000).toISOString()}`);
    if (oldest < cutoffTs - 3600) break;
    await sleep(PAGE_SLEEP_MS);
  }
  return dedupeTxs(all).filter((tx) => (tx.timestamp ?? 0) >= cutoffTs - 3600);
}

function dedupeTxs(txs) {
  const map = new Map();
  for (const tx of txs) if (tx?.signature) map.set(tx.signature, tx);
  return [...map.values()].sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0));
}

function extractSwap(tx, wallet) {
  let nativeNet = 0;
  const tokenNet = new Map();
  const decimalsByMint = new Map();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!mint || !Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
      decimalsByMint.set(mint, change.rawTokenAmount?.decimals ?? 0);
    }
  }

  let mint = null;
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
    decimals: decimalsByMint.get(mint) ?? 0,
  };
}

function buildCampaigns(swaps) {
  const campaigns = new Map();
  for (const swap of swaps.sort((a, b) => a.timestamp - b.timestamp)) {
    let c = campaigns.get(swap.mint);
    if (!c) {
      if (swap.side !== "buy") continue;
      c = {
        mint: swap.mint,
        firstBuy: swap,
        firstSell: null,
        spentLamports: 0,
        boughtRaw: 0,
        receivedLamports: 0,
        soldRaw: 0,
        decimals: swap.decimals,
        buys: [],
        sells: [],
      };
      campaigns.set(swap.mint, c);
    }
    if (swap.side === "buy") {
      c.spentLamports += swap.solLamports;
      c.boughtRaw += swap.tokenRaw;
      c.buys.push(swap);
    } else {
      c.receivedLamports += swap.solLamports;
      c.soldRaw += swap.tokenRaw;
      c.sells.push(swap);
      c.firstSell ??= swap;
    }
  }
  return [...campaigns.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

function sourceFillMarketCapUsd(campaign) {
  if (!SOL_USD || campaign.firstBuy.tokenRaw <= 0) return null;
  const uiTokens = campaign.firstBuy.tokenRaw / 10 ** campaign.decimals;
  if (!Number.isFinite(uiTokens) || uiTokens <= 0) return null;
  return (sol(campaign.firstBuy.solLamports) * SOL_USD * SUPPLY_APPROX) / uiTokens;
}

function sourceExitPct(campaign) {
  if (!campaign.firstSell || campaign.spentLamports <= 0 || campaign.boughtRaw <= 0 || campaign.firstSell.tokenRaw <= 0) return null;
  const avgEntry = campaign.spentLamports / campaign.boughtRaw;
  const firstSell = campaign.firstSell.solLamports / campaign.firstSell.tokenRaw;
  return ((firstSell - avgEntry) / avgEntry) * 100;
}

async function getTokenPairInfo(mint) {
  const pairs = await getTokenPairInfos(mint);
  return pairs[0] ?? null;
}

async function getTokenPairInfos(mint) {
  const listKey = `pairs-${mint}.json`;
  const cachedList = await readCache(listKey);
  if (Array.isArray(cachedList)) return cachedList;

  const key = `pair-${mint}.json`;
  const cached = await readCache(key);
  if (cached?.pairAddress) return [cached];

  const json = await fetchJson(`https://api.dexscreener.com/latest/dex/tokens/${mint}`, { timeoutMs: 10_000 });
  const pairs = (json.pairs ?? []).filter((pair) => pair.chainId === "solana" && pair.pairAddress);
  if (!pairs.length) {
    await writeCache(listKey, []);
    await writeCache(key, null);
    return [];
  }
  const out = pairs
    .sort((a, b) => (b.liquidity?.usd ?? 0) - (a.liquidity?.usd ?? 0))
    .map((pair) => ({
      pairAddress: pair.pairAddress,
      dexId: pair.dexId ?? null,
      url: pair.url ?? null,
      currentMarketCapUsd: typeof (pair.marketCap ?? pair.fdv) === "number" ? pair.marketCap ?? pair.fdv : null,
      liquidityUsd: typeof pair.liquidity?.usd === "number" ? pair.liquidity.usd : null,
      symbol: pair.baseToken?.address === mint ? pair.baseToken?.symbol ?? "" : pair.quoteToken?.symbol ?? "",
    }));
  await writeCache(listKey, out);
  await writeCache(key, out[0] ?? null);
  return out;
}

async function fetchGeckoOhlcv(pairAddress, entryTs) {
  const key = `gecko-${pairAddress}-${entryTs}-${HORIZON_HOURS}.json`;
  const cached = await readCache(key);
  if (cached) return cached;

  const untilTs = entryTs + HORIZON_HOURS * 3600;
  const rows = [];
  let before = untilTs + 60;
  for (let page = 0; page < 4; page++) {
    const url =
      `${GECKO_BASE}/networks/solana/pools/${pairAddress}/ohlcv/minute` +
      `?aggregate=1&before_timestamp=${before}&limit=1000&currency=usd`;
    const json = await fetchJson(url, { timeoutMs: 15_000 });
    const pageRows = json?.data?.attributes?.ohlcv_list ?? [];
    if (!pageRows.length) break;
    for (const row of pageRows) {
      const [ts, open, high, low, close, volume] = row;
      if (ts >= entryTs - 60 && ts <= untilTs + 60) rows.push({ ts, open, high, low, close, volume });
    }
    before = Math.min(...pageRows.map((row) => row[0])) - 1;
    if (before <= entryTs - 60) break;
    await sleep(350);
  }
  const sorted = rows
    .filter((row) => Number.isFinite(row.ts) && Number.isFinite(row.open) && Number.isFinite(row.close))
    .sort((a, b) => a.ts - b.ts);
  await writeCache(key, sorted);
  return sorted;
}

function candleAtOrAfter(candles, ts) {
  return candles.find((candle) => candle.ts >= ts) ?? null;
}

function roiFromPrice(entryPrice, price) {
  return entryPrice > 0 && price > 0 ? ((price - entryPrice) / entryPrice) * 100 : null;
}

function analyzePath(campaign, candles) {
  const entry = candleAtOrAfter(candles, campaign.firstBuy.timestamp);
  if (!entry) return null;
  const sourceExitTs = campaign.firstSell?.timestamp ?? campaign.firstBuy.timestamp + HORIZON_HOURS * 3600;
  const endTs = Math.min(sourceExitTs, campaign.firstBuy.timestamp + HORIZON_HOURS * 3600);
  const path = candles.filter((candle) => candle.ts >= entry.ts && candle.ts <= endTs);
  if (!path.length) return null;
  const entryPrice = entry.open || entry.close;
  const mfePct = Math.max(...path.map((candle) => roiFromPrice(entryPrice, candle.high)).filter(Number.isFinite));
  const maePct = Math.min(...path.map((candle) => roiFromPrice(entryPrice, candle.low)).filter(Number.isFinite));

  const hit = {};
  const hitTimeSec = {};
  for (const target of TARGETS) {
    const targetCandle = path.find((candle) => (roiFromPrice(entryPrice, candle.high) ?? -Infinity) >= target);
    hit[`tp${target}`] = Boolean(targetCandle);
    hitTimeSec[`tp${target}`] = targetCandle ? targetCandle.ts - campaign.firstBuy.timestamp : null;
  }

  let peak = -Infinity;
  let activeFloor = null;
  let trailingExit = null;
  for (const candle of path) {
    peak = Math.max(peak, roiFromPrice(entryPrice, candle.high) ?? -Infinity);
    for (const step of TRAILING_STEPS) {
      if (peak >= step.peak) activeFloor = activeFloor == null ? step.floor : Math.max(activeFloor, step.floor);
    }
    if (activeFloor != null && (roiFromPrice(entryPrice, candle.low) ?? Infinity) <= activeFloor) {
      trailingExit = {
        roiPct: activeFloor,
        timeSec: candle.ts - campaign.firstBuy.timestamp,
        peakPct: peak,
        floorPct: activeFloor,
      };
      break;
    }
  }

  return { entryPrice, mfePct, maePct, hit, hitTimeSec, trailingExit };
}

function summarizeReturns(name, returns) {
  const xs = returns.filter(Number.isFinite);
  const wins = xs.filter((value) => value > 0);
  const losses = xs.filter((value) => value < 0);
  const grossWin = wins.reduce((sum, value) => sum + value, 0);
  const grossLoss = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  return {
    name,
    trades: xs.length,
    winRatePct: xs.length ? (wins.length / xs.length) * 100 : null,
    avgPct: avg(xs),
    medianPct: median(xs),
    p25Pct: quantile(xs, 0.25),
    p75Pct: quantile(xs, 0.75),
    totalPct: xs.reduce((sum, value) => sum + value, 0),
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
  };
}

function markdown(output) {
  const strategyRows = output.strategySummaries
    .map((s) => `| ${s.name} | ${s.trades} | ${fmtPct(s.winRatePct)} | ${fmtPct(s.avgPct)} | ${fmtPct(s.medianPct)} | ${s.profitFactor === Infinity ? "inf" : s.profitFactor.toFixed(2)} | ${fmtPct(s.totalPct)} |`)
    .join("\n");
  const targetRows = output.targetHitSummary
    .map((s) => `| +${s.targetPct}% | ${s.hits}/${s.trades} | ${fmtPct(s.hitRatePct)} | ${s.medianHitMin == null ? "-" : s.medianHitMin.toFixed(1)} |`)
    .join("\n");
  const tradeRows = output.rows
    .map((row) => `| ${row.symbol || short(row.mint)} | \`${short(row.mint)}\` | ${row.entryIso} | ${fmtUsd(row.sourceFillMarketCapUsd)} | ${fmtSol(row.sourceBuySol)} | ${fmtPct(row.sourceExitPct)} | ${fmtPct(row.mfePct)} | ${fmtPct(row.maePct)} | ${row.trailingExit ? `${fmtPct(row.trailingExit.roiPct)} @ ${Math.round(row.trailingExit.timeSec / 60)}m` : "-"} |`)
    .join("\n");

  const scope =
    output.routeFilter === "all"
      ? ""
      : " PumpFun/PumpSwap-Only";

  const windowLabel =
    output.windowStartHoursAgo > 0
      ? `${output.windowStartHoursAgo}h-${output.windowEndHoursAgo}h Ago`
      : `Last ${HOURS}h`;

  return `# CyA ${windowLabel} MC >= ${fmtUsd(MIN_ENTRY_MC_USD)}${scope} Analysis

Generated: ${output.generatedAt}

Assumptions:
- Fresh Helius wallet scan; one campaign per mint.
- Time window: ${output.windowStartHoursAgo > 0 ? `${output.windowStartIso} through ${output.windowEndIso}` : `last ${HOURS}h`}.
- Route filter: ${output.routeFilter === "all" ? "all reconstructed swaps" : "PumpFun/PumpSwap mints only (`...pump`)."}
- Entry MC is inferred from CyA's first fill, SOL/USD=${SOL_USD}, supply=${SUPPLY_APPROX.toLocaleString("en-US")}.
- TP rows sell 100% if target is hit before CyA's first sell; otherwise they fall back to CyA first sell.
- Trailing row uses \`${TRAILING_STEPS.map((s) => `${s.peak}:${s.floor}`).join(",")}\` before CyA's first sell.

## Coverage

- Wallet txs fetched: ${output.coverage.walletTxs}
- Swaps reconstructed: ${output.coverage.swaps}
- Campaigns in analysis window: ${output.coverage.campaigns48h}
- MC >= ${fmtUsd(MIN_ENTRY_MC_USD)} candidates before route filter: ${output.coverage.mcCandidates}
- Candidates after route filter: ${output.coverage.candidates}
- Measured rows: ${output.rows.length}
- Skips: ${output.skips.length}
- Median entry MC: ${fmtUsd(output.entryMc.median)}
- Median MFE/MAE: ${fmtPct(output.path.medianMfePct)} / ${fmtPct(output.path.medianMaePct)}

## Target Hit Before CyA First Sell

| Target | Hits | Hit Rate | Median Hit Min |
|---|---:|---:|---:|
${targetRows}

## Strategy Comparison

| Strategy | Trades | Win | Avg | Median | PF | Total |
|---|---:|---:|---:|---:|---:|---:|
${strategyRows}

## Trades

| Token | Mint | Entry UTC | Entry MC | CyA Buy | CyA Exit | MFE | MAE | Trailing Exit |
|---|---|---:|---:|---:|---:|---:|---:|---:|
${tradeRows}

## Skips

${output.skips.map((skip) => `- \`${short(skip.mint)}\`: ${skip.reason}`).join("\n") || "None"}
`;
}

async function main() {
  const nowTs = Math.floor(Date.now() / 1000);
  const windowEndTs = nowTs - WINDOW_START_HOURS_AGO * 3600;
  const cutoffTs = nowTs - (WINDOW_START_HOURS_AGO + HOURS) * 3600;
  const txs = await fetchWalletTxsSince(WALLET, cutoffTs);
  const swaps = txs.map((tx) => extractSwap(tx, WALLET)).filter(Boolean);
  const campaigns48h = buildCampaigns(swaps).filter(
    (campaign) => campaign.firstBuy.timestamp >= cutoffTs && campaign.firstBuy.timestamp < windowEndTs
  );
  const mcCandidates = campaigns48h
    .map((campaign) => ({ campaign, sourceFillMarketCapUsd: sourceFillMarketCapUsd(campaign) }))
    .filter((row) => (row.sourceFillMarketCapUsd ?? 0) >= MIN_ENTRY_MC_USD);
  const candidates = mcCandidates.filter((row) => matchesRouteFilter(row.campaign));

  const rows = [];
  const skips = [];
  for (const [index, candidate] of candidates.entries()) {
    const { campaign } = candidate;
    try {
      const pairs = await getTokenPairInfos(campaign.mint);
      if (!pairs.length) {
        skips.push({ mint: campaign.mint, reason: "no DexScreener/Gecko pair" });
        continue;
      }
      let pair = null;
      let candles = null;
      let path = null;
      const pairFailures = [];
      for (const candidatePair of pairs) {
        try {
          const candidateCandles = await fetchGeckoOhlcv(candidatePair.pairAddress, campaign.firstBuy.timestamp);
          const candidatePath = analyzePath(campaign, candidateCandles);
          if (candidatePath) {
            pair = candidatePair;
            candles = candidateCandles;
            path = candidatePath;
            break;
          }
          pairFailures.push(`${candidatePair.dexId ?? "unknown"}:${short(candidatePair.pairAddress)} no entry/path candles`);
        } catch (err) {
          pairFailures.push(
            `${candidatePair.dexId ?? "unknown"}:${short(candidatePair.pairAddress)} ${
              err instanceof Error ? err.message : String(err)
            }`
          );
        }
        await sleep(350);
      }
      if (!path) {
        skips.push({
          mint: campaign.mint,
          reason: `missing entry/path candles across ${pairs.length} pair(s): ${pairFailures.slice(0, 4).join("; ")}`,
        });
        continue;
      }
      const sourceExit = sourceExitPct(campaign);
      const fallbackExit = sourceExit ?? roiFromPrice(path.entryPrice, candles.at(-1)?.close ?? path.entryPrice) ?? 0;
      const strategyReturns = {
        mirrorCyaFirstSell: fallbackExit,
        tp30Sell100: path.hit.tp30 ? 30 : fallbackExit,
        tp50Sell100: path.hit.tp50 ? 50 : fallbackExit,
        tp75Sell100: path.hit.tp75 ? 75 : fallbackExit,
        tp100Sell100: path.hit.tp100 ? 100 : fallbackExit,
        trailingCustom: path.trailingExit?.roiPct ?? fallbackExit,
      };
      rows.push({
        mint: campaign.mint,
        symbol: pair.symbol,
        entryIso: new Date(campaign.firstBuy.timestamp * 1000).toISOString(),
        sourceFillMarketCapUsd: candidate.sourceFillMarketCapUsd,
        sourceBuySol: sol(campaign.firstBuy.solLamports),
        sourceExitPct: sourceExit,
        firstSellMinutes: campaign.firstSell ? (campaign.firstSell.timestamp - campaign.firstBuy.timestamp) / 60 : null,
        currentMarketCapUsd: pair.currentMarketCapUsd,
        liquidityUsd: pair.liquidityUsd,
        mfePct: path.mfePct,
        maePct: path.maePct,
        hit: path.hit,
        hitTimeSec: path.hitTimeSec,
        trailingExit: path.trailingExit,
        strategyReturns,
        firstBuySignature: campaign.firstBuy.signature,
        firstSellSignature: campaign.firstSell?.signature ?? null,
      });
      console.log(`${index + 1}/${candidates.length} ${pair.symbol || short(campaign.mint)} measured`);
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err);
      skips.push({ mint: campaign.mint, reason });
      console.warn(`${index + 1}/${candidates.length} ${short(campaign.mint)} skipped: ${reason}`);
    }
    await sleep(CANDIDATE_SLEEP_MS);
  }

  const strategySummaries = [
    summarizeReturns("mirror CyA first sell", rows.map((row) => row.strategyReturns.mirrorCyaFirstSell)),
    summarizeReturns("TP30 sell 100%", rows.map((row) => row.strategyReturns.tp30Sell100)),
    summarizeReturns("TP50 sell 100%", rows.map((row) => row.strategyReturns.tp50Sell100)),
    summarizeReturns("TP75 sell 100%", rows.map((row) => row.strategyReturns.tp75Sell100)),
    summarizeReturns("TP100 sell 100%", rows.map((row) => row.strategyReturns.tp100Sell100)),
    summarizeReturns("custom trailing", rows.map((row) => row.strategyReturns.trailingCustom)),
  ];
  const targetHitSummary = TARGETS.map((target) => {
    const hits = rows.filter((row) => row.hit[`tp${target}`]);
    return {
      targetPct: target,
      hits: hits.length,
      trades: rows.length,
      hitRatePct: rows.length ? (hits.length / rows.length) * 100 : null,
      medianHitMin: median(hits.map((row) => row.hitTimeSec[`tp${target}`] / 60)),
    };
  });
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: WALLET,
    hours: HOURS,
    windowStartHoursAgo: WINDOW_START_HOURS_AGO,
    windowEndHoursAgo: WINDOW_START_HOURS_AGO + HOURS,
    windowStartIso: new Date(cutoffTs * 1000).toISOString(),
    windowEndIso: new Date(windowEndTs * 1000).toISOString(),
    minEntryMcUsd: MIN_ENTRY_MC_USD,
    horizonHours: HORIZON_HOURS,
    routeFilter: ROUTE_FILTER,
    solUsd: SOL_USD,
    supplyApprox: SUPPLY_APPROX,
    trailingSteps: TRAILING_STEPS,
    coverage: {
      walletTxs: txs.length,
      swaps: swaps.length,
      campaigns48h: campaigns48h.length,
      mcCandidates: mcCandidates.length,
      candidates: candidates.length,
    },
    entryMc: {
      median: median(rows.map((row) => row.sourceFillMarketCapUsd)),
      p25: quantile(rows.map((row) => row.sourceFillMarketCapUsd), 0.25),
      p75: quantile(rows.map((row) => row.sourceFillMarketCapUsd), 0.75),
    },
    path: {
      medianMfePct: median(rows.map((row) => row.mfePct)),
      medianMaePct: median(rows.map((row) => row.maePct)),
    },
    targetHitSummary,
    strategySummaries,
    rows: rows.sort((a, b) => b.sourceFillMarketCapUsd - a.sourceFillMarketCapUsd),
    skips,
  };

  await fs.writeFile(OUT_JSON, JSON.stringify(output, null, 2), "utf8");
  await fs.writeFile(OUT_MD, markdown(output), "utf8");
  console.log(JSON.stringify({
    rows: output.rows.length,
    coverage: output.coverage,
    targetHitSummary,
    strategySummaries,
    outputJson: OUT_JSON,
    outputMd: OUT_MD,
  }, null, 2));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.stack ?? err.message : String(err));
  process.exit(1);
});
