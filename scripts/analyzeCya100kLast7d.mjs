import fs from "node:fs/promises";
import path from "node:path";

const WALLET = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const GECKO_BASE = "https://api.geckoterminal.com/api/v2";
const CACHE_DIR = path.resolve(process.cwd(), "data", "cya-100k-last7d-cache");
const WALLET_CACHE = path.resolve(
  process.cwd(),
  "data",
  "wallet-quant-audit-cache",
  `wallet-${WALLET}-10000.json`
);
const OUT_JSON = path.resolve(process.cwd(), "data", "cya-100k-last7d.json");
const OUT_MD = path.resolve(process.cwd(), "data", "cya-100k-last7d.md");
const GECKO_PAGE_SLEEP_MS = Number(process.env.GECKO_PAGE_SLEEP_MS ?? "3500");
const GECKO_CANDIDATE_SLEEP_MS = Number(process.env.GECKO_CANDIDATE_SLEEP_MS ?? "12000");
const GECKO_429_SLEEP_MS = Number(process.env.GECKO_429_SLEEP_MS ?? "65000");

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function short(value) {
  if (!value) return "-";
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function sol(lamports) {
  return lamports / 1_000_000_000;
}

function pct(from, to) {
  return ((to - from) / from) * 100;
}

function avg(values) {
  return values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
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

function fmtPctPoints(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}pp`;
}

function fmtUsd(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  return `$${Math.round(value).toLocaleString()}`;
}

function fmtSol(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)} SOL`;
}

async function cacheGet(key) {
  try {
    return JSON.parse(await fs.readFile(path.join(CACHE_DIR, key), "utf8"));
  } catch {
    return null;
  }
}

async function cacheSet(key, value) {
  await fs.mkdir(CACHE_DIR, { recursive: true });
  await fs.writeFile(path.join(CACHE_DIR, key), JSON.stringify(value), "utf8");
}

async function fetchJsonCached(key, fetcher) {
  const cached = await cacheGet(key);
  if (cached) return cached;
  const value = await fetcher();
  await cacheSet(key, value);
  return value;
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
      if (!mint || mint === WRAPPED_SOL_MINT) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
      decimalsByMint.set(mint, change.rawTokenAmount?.decimals ?? 0);
    }
  }

  let mint = null;
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
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "buy",
      mint,
      solLamports: Math.abs(nativeNet),
      tokenRaw: Math.abs(tokenRaw),
      decimals: decimalsByMint.get(mint) ?? 0,
    };
  }
  if (nativeNet > 0 && tokenRaw < 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "sell",
      mint,
      solLamports: nativeNet,
      tokenRaw: Math.abs(tokenRaw),
      decimals: decimalsByMint.get(mint) ?? 0,
    };
  }
  return null;
}

function buildCampaigns(swaps) {
  const byMint = new Map();
  for (const swap of swaps.sort((a, b) => a.timestamp - b.timestamp)) {
    let campaign = byMint.get(swap.mint);
    if (!campaign) {
      if (swap.side !== "buy") continue;
      campaign = {
        mint: swap.mint,
        firstBuy: swap,
        firstSell: null,
        buys: [],
        sells: [],
        spentLamports: 0,
        boughtRaw: 0,
        receivedLamports: 0,
        soldRaw: 0,
        decimals: swap.decimals,
      };
      byMint.set(swap.mint, campaign);
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
  return [...byMint.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

function sourceExitPct(campaign) {
  if (
    campaign.spentLamports <= 0 ||
    campaign.boughtRaw <= 0 ||
    campaign.receivedLamports <= 0 ||
    campaign.soldRaw <= 0
  ) {
    return null;
  }
  const costSold = (campaign.spentLamports / campaign.boughtRaw) * Math.min(campaign.soldRaw, campaign.boughtRaw);
  return costSold > 0 ? ((campaign.receivedLamports - costSold) / costSold) * 100 : null;
}

function realizedPnlSol(campaign) {
  if (campaign.spentLamports <= 0 || campaign.boughtRaw <= 0 || campaign.receivedLamports <= 0 || campaign.soldRaw <= 0) {
    return null;
  }
  const costSold = (campaign.spentLamports / campaign.boughtRaw) * Math.min(campaign.soldRaw, campaign.boughtRaw);
  return sol(campaign.receivedLamports - costSold);
}

function sourceFillMarketCap(campaign, solUsd) {
  if (!solUsd || campaign.boughtRaw <= 0 || campaign.spentLamports <= 0) return null;
  const uiTokens = campaign.boughtRaw / 10 ** campaign.decimals;
  if (uiTokens <= 0) return null;
  return (sol(campaign.spentLamports) * solUsd * 1_000_000_000) / uiTokens;
}

async function fetchSolUsd() {
  const cached = await cacheGet("sol-usd.json");
  if (cached?.value && Date.now() - cached.fetchedAt < 30 * 60 * 1000) return cached.value;
  const response = await fetch("https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd", {
    signal: AbortSignal.timeout(8_000),
  });
  if (!response.ok) throw new Error(`Coingecko ${response.status}: ${(await response.text()).slice(0, 120)}`);
  const json = await response.json();
  const value = json?.solana?.usd;
  if (typeof value !== "number" || value <= 0) throw new Error("SOL/USD unavailable");
  await cacheSet("sol-usd.json", { value, fetchedAt: Date.now() });
  return value;
}

async function getTokenPairInfo(mint) {
  return fetchJsonCached(`pair-${mint}.json`, async () => {
    const response = await fetch(`https://api.dexscreener.com/latest/dex/tokens/${mint}`, {
      signal: AbortSignal.timeout(8_000),
    });
    if (!response.ok) return null;
    const json = await response.json();
    const pairs = (json.pairs ?? []).filter((pair) => pair.chainId === "solana" && pair.pairAddress);
    if (!pairs.length) return null;
    const best = pairs.reduce((a, b) => ((b.liquidity?.usd ?? 0) > (a.liquidity?.usd ?? 0) ? b : a));
    return {
      mint,
      dexId: best.dexId ?? "unknown",
      pairAddress: best.pairAddress,
      url: best.url ?? null,
      liquidityUsd: typeof best.liquidity?.usd === "number" ? best.liquidity.usd : null,
      currentMarketCapUsd: typeof (best.marketCap ?? best.fdv) === "number" ? best.marketCap ?? best.fdv : null,
      symbol: best.baseToken?.address === mint ? best.baseToken?.symbol ?? "" : best.quoteToken?.symbol ?? "",
      name: best.baseToken?.address === mint ? best.baseToken?.name ?? "" : best.quoteToken?.name ?? "",
    };
  });
}

async function fetchGeckoOhlcv(poolAddress, entryTs, horizonHours) {
  const untilTs = entryTs + horizonHours * 3600;
  return fetchJsonCached(`gecko-${poolAddress}-${entryTs}-${horizonHours}.json`, async () => {
    const rows = [];
    let before = untilTs + 60;
    for (let page = 0; page < 8; page++) {
      const url =
        `${GECKO_BASE}/networks/solana/pools/${poolAddress}/ohlcv/minute` +
        `?aggregate=1&before_timestamp=${before}&limit=1000&currency=usd`;
      let response = await fetch(url, { signal: AbortSignal.timeout(12_000) });
      for (let attempt = 1; response.status === 429 && attempt <= 4; attempt++) {
        await sleep(GECKO_429_SLEEP_MS);
        response = await fetch(url, { signal: AbortSignal.timeout(12_000) });
      }
      if (!response.ok) throw new Error(`GeckoTerminal ${response.status}: ${(await response.text()).slice(0, 160)}`);
      const json = await response.json();
      const batch = json.data?.attributes?.ohlcv_list ?? [];
      if (!batch.length) break;
      for (const row of batch) {
        const [ts, open, high, low, close, volume] = row;
        if ([ts, open, high, low, close].some((v) => typeof v !== "number")) continue;
        if (ts >= entryTs - 300 && ts <= untilTs) rows.push({ ts, open, high, low, close, volume: volume ?? 0 });
      }
      const oldest = Math.min(...batch.map((row) => row[0] ?? before));
      if (!Number.isFinite(oldest) || oldest <= entryTs - 300) break;
      before = oldest - 1;
      await sleep(GECKO_PAGE_SLEEP_MS);
    }
    return rows.sort((a, b) => a.ts - b.ts);
  });
}

function candleAtOrAfter(candles, ts) {
  return candles.find((candle) => candle.ts >= ts) ?? candles.find((candle) => candle.ts >= ts - 60) ?? null;
}

function returnsPath(row) {
  const after = row.candles.filter((candle) => candle.ts >= row.entryTs);
  if (!after.length) return { mfe: null, mae: null, endPct: null };
  const high = Math.max(...after.map((candle) => candle.high));
  const low = Math.min(...after.map((candle) => candle.low));
  const end = after[after.length - 1].close;
  return { mfe: pct(row.entryPrice, high), mae: pct(row.entryPrice, low), endPct: pct(row.entryPrice, end) };
}

function fixedTpSl(row, tp, sl) {
  for (const candle of row.candles.filter((c) => c.ts >= row.entryTs)) {
    if (pct(row.entryPrice, candle.low) <= -sl) return -sl;
    if (pct(row.entryPrice, candle.high) >= tp) return tp;
  }
  return row.endPct ?? row.sourceExitPct ?? 0;
}

function trailing(row, givebackPct) {
  let peak = row.entryPrice;
  for (const candle of row.candles.filter((c) => c.ts >= row.entryTs)) {
    peak = Math.max(peak, candle.high);
    const stop = peak * (1 - givebackPct / 100);
    if (candle.low <= stop) return pct(row.entryPrice, stop);
  }
  return row.endPct ?? row.sourceExitPct ?? 0;
}

function timeExit(row, minutes) {
  const targetTs = row.entryTs + minutes * 60;
  const candle = row.candles.find((c) => c.ts >= targetTs) ?? row.candles[row.candles.length - 1];
  return candle ? pct(row.entryPrice, candle.close) : row.sourceExitPct ?? 0;
}

function halfTpHalfSource(row) {
  const hit30 = row.mfePct != null && row.mfePct >= 30;
  const source = row.sourceExitPct ?? 0;
  return hit30 ? 15 + source * 0.5 : source;
}

function summarizeStrategy(name, returns) {
  const xs = returns.filter(Number.isFinite);
  const wins = xs.filter((value) => value > 0);
  const losses = xs.filter((value) => value < 0);
  const grossWin = wins.reduce((sum, value) => sum + value, 0);
  const grossLoss = Math.abs(losses.reduce((sum, value) => sum + value, 0));
  let equity = 0;
  let peak = 0;
  let maxDrawdownPctPoints = 0;
  for (const value of xs) {
    equity += value;
    peak = Math.max(peak, equity);
    maxDrawdownPctPoints = Math.min(maxDrawdownPctPoints, equity - peak);
  }
  return {
    name,
    trades: xs.length,
    winRatePct: (wins.length / Math.max(1, xs.length)) * 100,
    avgPct: avg(xs),
    medianPct: median(xs),
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? null : 0,
    maxDrawdownPctPoints,
    totalPct: xs.reduce((sum, value) => sum + value, 0),
  };
}

function markdown(output) {
  const lines = [
    "# CyA 100k+ Last 7d Analysis",
    "",
    `Generated: ${output.generatedAt}`,
    `Wallet: \`${output.wallet}\``,
    `Window: ${output.window.startIso} to ${output.window.endIso}`,
    "",
    "## Coverage",
    "",
    `- Wallet txs loaded: ${output.coverage.txsLoaded}`,
    `- Last-7d campaigns: ${output.coverage.last7dCampaigns}`,
    `- Source-fill 100k+ candidates: ${output.coverage.sourceFillCandidates}`,
    `- Measured 100k+ entries: ${output.coverage.measuredRows}`,
    `- Skipped candidates: ${output.coverage.skipped}`,
    `- SOL/USD used for source-fill prefilter: ${fmtUsd(output.solUsd)}`,
    "",
    "## Summary",
    "",
    `- Realized PnL: ${fmtSol(output.summary.realizedPnlSol)}`,
    `- Source-exit win rate: ${fmtPct(output.summary.sourceWinRatePct)}`,
    `- Avg source ROI: ${fmtPct(output.summary.avgSourceExitPct)}`,
    `- Median source ROI: ${fmtPct(output.summary.medianSourceExitPct)}`,
    `- Profit factor: ${output.summary.profitFactor == null ? "Infinity" : output.summary.profitFactor.toFixed(2)}`,
    `- Median entry MC: ${fmtUsd(output.summary.medianEntryMcUsd)}`,
    `- Median MFE / MAE: ${fmtPct(output.summary.medianMfePct)} / ${fmtPct(output.summary.medianMaePct)}`,
    `- Hit +30 / +50 / +100: ${fmtPct(output.summary.hit30Pct)} / ${fmtPct(output.summary.hit50Pct)} / ${fmtPct(output.summary.hit100Pct)}`,
    "",
    "## Strategy Backtest",
    "",
    "Returns are per-trade ROI. Cumulative DD is the worst drop in the cumulative return curve, expressed in percentage points, not bankroll percent.",
    "",
    "| Strategy | Trades | Win % | Avg ROI | Median ROI | Profit factor | Cumulative DD |",
    "|---|---:|---:|---:|---:|---:|---:|",
    ...output.strategies.map((row) =>
      `| ${row.name} | ${row.trades} | ${fmtPct(row.winRatePct)} | ${fmtPct(row.avgPct)} | ${fmtPct(row.medianPct)} | ${
        row.profitFactor == null ? "Infinity" : row.profitFactor.toFixed(2)
      } | ${fmtPctPoints(row.maxDrawdownPctPoints)} |`
    ),
    "",
    "## Trades",
    "",
    "| Token | Entry UTC | Buy SOL | Entry MC | Source ROI | MFE | MAE | +100 Time | Current MC |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...output.rows.map((row) =>
      `| ${row.symbol || short(row.mint)} (\`${short(row.mint)}\`) | ${row.entryIso} | ${row.sourceBuySol.toFixed(3)} | ${fmtUsd(
        row.entryMarketCapUsd
      )} | ${fmtPct(row.sourceExitPct)} | ${fmtPct(row.mfePct)} | ${fmtPct(row.maePct)} | ${
        row.timeTo100Sec == null ? "-" : `${Math.round(row.timeTo100Sec / 60)}m`
      } | ${fmtUsd(row.currentMarketCapUsd)} |`
    ),
    "",
    "## Skips",
    "",
    ...output.skips.map((skip) => `- ${short(skip.mint)}: ${skip.reason}`),
    "",
  ];
  return `${lines.join("\n")}\n`;
}

async function main() {
  const thresholdUsd = Number(process.argv[2] ?? "100000");
  const days = Number(process.argv[3] ?? "7");
  const horizonHours = Number(process.argv[4] ?? "24");
  const txs = JSON.parse(await fs.readFile(WALLET_CACHE, "utf8"));
  const newestTs = Math.max(...txs.map((tx) => tx.timestamp ?? 0));
  const cutoffTs = newestTs - days * 86400;
  const solUsd = await fetchSolUsd();
  const swaps = txs.map((tx) => extractSwap(tx, WALLET)).filter(Boolean);
  const campaigns = buildCampaigns(swaps);
  const last7d = campaigns.filter((campaign) => campaign.firstBuy.timestamp >= cutoffTs);
  const sourceFillCandidates = last7d
    .map((campaign) => ({ campaign, sourceFillMarketCapUsd: sourceFillMarketCap(campaign, solUsd) }))
    .filter((row) => (row.sourceFillMarketCapUsd ?? 0) >= thresholdUsd);

  const rows = [];
  const skips = [];
  for (const [index, candidate] of sourceFillCandidates.entries()) {
    const campaign = candidate.campaign;
    try {
      const pair = await getTokenPairInfo(campaign.mint);
      if (!pair?.pairAddress) {
        skips.push({ mint: campaign.mint, reason: "no DexScreener pair" });
        continue;
      }
      const candles = await fetchGeckoOhlcv(pair.pairAddress, campaign.firstBuy.timestamp, horizonHours);
      const entryCandle = candleAtOrAfter(candles, campaign.firstBuy.timestamp);
      if (!entryCandle || entryCandle.open <= 0) {
        skips.push({ mint: campaign.mint, reason: "no entry candle" });
        continue;
      }
      const entryMarketCapUsd = entryCandle.open * 1_000_000_000;
      if (entryMarketCapUsd < thresholdUsd) {
        skips.push({
          mint: campaign.mint,
          reason: `entry candle MC ${fmtUsd(entryMarketCapUsd)} below ${fmtUsd(thresholdUsd)}`,
        });
        continue;
      }
      const after = candles.filter((candle) => candle.ts >= campaign.firstBuy.timestamp);
      const pathStats = returnsPath({
        candles: after,
        entryTs: campaign.firstBuy.timestamp,
        entryPrice: entryCandle.open,
      });
      const hit100 = after.find((candle) => pct(entryCandle.open, candle.high) >= 100);
      rows.push({
        mint: campaign.mint,
        symbol: pair.symbol || short(campaign.mint),
        dexId: pair.dexId,
        entryIso: new Date(campaign.firstBuy.timestamp * 1000).toISOString(),
        entryTs: campaign.firstBuy.timestamp,
        sourceBuySol: sol(campaign.firstBuy.solLamports),
        totalSourceSpentSol: sol(campaign.spentLamports),
        sourceFillMarketCapUsd: candidate.sourceFillMarketCapUsd,
        entryMarketCapUsd,
        currentMarketCapUsd: pair.currentMarketCapUsd,
        currentLiquidityUsd: pair.liquidityUsd,
        sourceExitPct: sourceExitPct(campaign),
        realizedPnlSol: realizedPnlSol(campaign),
        buys: campaign.buys.length,
        sells: campaign.sells.length,
        holdMinutes:
          campaign.firstSell == null ? null : (campaign.firstSell.timestamp - campaign.firstBuy.timestamp) / 60,
        entryPrice: entryCandle.open,
        candles: after,
        mfePct: pathStats.mfe,
        maePct: pathStats.mae,
        endPct: pathStats.endPct,
        timeTo100Sec: hit100 ? hit100.ts - campaign.firstBuy.timestamp : null,
      });
      console.log(`${index + 1}/${sourceFillCandidates.length} ${pair.symbol || short(campaign.mint)} measured`);
    } catch (err) {
      skips.push({ mint: campaign.mint, reason: err instanceof Error ? err.message : String(err) });
    }
    await sleep(GECKO_CANDIDATE_SLEEP_MS);
  }

  const sourceReturns = rows.map((row) => row.sourceExitPct).filter(Number.isFinite);
  const grossWin = rows.filter((row) => (row.realizedPnlSol ?? 0) > 0).reduce((sum, row) => sum + row.realizedPnlSol, 0);
  const grossLoss = Math.abs(rows.filter((row) => (row.realizedPnlSol ?? 0) < 0).reduce((sum, row) => sum + row.realizedPnlSol, 0));
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: WALLET,
    thresholdUsd,
    solUsd,
    window: {
      days,
      startIso: new Date(cutoffTs * 1000).toISOString(),
      endIso: new Date(newestTs * 1000).toISOString(),
      horizonHours,
    },
    coverage: {
      txsLoaded: txs.length,
      last7dCampaigns: last7d.length,
      sourceFillCandidates: sourceFillCandidates.length,
      measuredRows: rows.length,
      skipped: skips.length,
    },
    summary: {
      realizedPnlSol: rows.reduce((sum, row) => sum + (row.realizedPnlSol ?? 0), 0),
      sourceWinRatePct: (sourceReturns.filter((value) => value > 0).length / Math.max(1, sourceReturns.length)) * 100,
      avgSourceExitPct: avg(sourceReturns),
      medianSourceExitPct: median(sourceReturns),
      profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? null : 0,
      medianEntryMcUsd: median(rows.map((row) => row.entryMarketCapUsd)),
      medianMfePct: median(rows.map((row) => row.mfePct ?? NaN)),
      medianMaePct: median(rows.map((row) => row.maePct ?? NaN)),
      hit30Pct: (rows.filter((row) => (row.mfePct ?? -Infinity) >= 30).length / Math.max(1, rows.length)) * 100,
      hit50Pct: (rows.filter((row) => (row.mfePct ?? -Infinity) >= 50).length / Math.max(1, rows.length)) * 100,
      hit100Pct: (rows.filter((row) => (row.mfePct ?? -Infinity) >= 100).length / Math.max(1, rows.length)) * 100,
      p25EntryMcUsd: quantile(rows.map((row) => row.entryMarketCapUsd), 0.25),
      p75EntryMcUsd: quantile(rows.map((row) => row.entryMarketCapUsd), 0.75),
    },
    strategies: [
      summarizeStrategy("Sell when CyA sells", rows.map((row) => row.sourceExitPct ?? 0)),
      summarizeStrategy("50% at +30, 50% CyA exit", rows.map(halfTpHalfSource)),
      summarizeStrategy("20% trailing stop", rows.map((row) => trailing(row, 20))),
      summarizeStrategy("TP30 SL20", rows.map((row) => fixedTpSl(row, 30, 20))),
      summarizeStrategy("TP50 SL25", rows.map((row) => fixedTpSl(row, 50, 25))),
      summarizeStrategy("Time exit 30m", rows.map((row) => timeExit(row, 30))),
      summarizeStrategy("Time exit 1h", rows.map((row) => timeExit(row, 60))),
      summarizeStrategy("Time exit 4h", rows.map((row) => timeExit(row, 240))),
      summarizeStrategy("Time exit 24h", rows.map((row) => timeExit(row, 1440))),
    ].sort((a, b) => b.avgPct - a.avgPct),
    rows: rows.sort((a, b) => b.entryTs - a.entryTs).map((row) => ({ ...row, candles: undefined })),
    skips,
  };

  await fs.writeFile(OUT_JSON, JSON.stringify(output, null, 2), "utf8");
  await fs.writeFile(OUT_MD, markdown(output), "utf8");
  console.log(markdown(output));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
