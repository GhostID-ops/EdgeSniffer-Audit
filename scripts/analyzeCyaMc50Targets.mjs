import fs from "node:fs/promises";
import path from "node:path";

const WALLET = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const GECKO_BASE = "https://api.geckoterminal.com/api/v2";
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-quant-audit-cache");
const GECKO_CACHE_DIR = path.resolve(process.cwd(), "data", "cya-100k-last7d-cache");
const WALLET_CACHE = path.join(CACHE_DIR, `wallet-${WALLET}-10000.json`);
const SOL_USD_CACHE = path.join(GECKO_CACHE_DIR, "sol-usd.json");
const OUT_JSON = path.resolve(process.cwd(), "data", "cya-mc50-target-exit-comparison.json");
const OUT_MD = path.resolve(process.cwd(), "data", "cya-mc50-target-exit-comparison.md");
const MIN_ENTRY_MC_USD = Number(process.argv[2] ?? "50000");
const HORIZON_HOURS = Number(process.argv[3] ?? "24");
const TARGETS = [30, 50, 75, 100];
const SUPPLY_APPROX = 1_000_000_000;
const GECKO_PAGE_SLEEP_MS = 450;
const GECKO_CANDIDATE_SLEEP_MS = 900;
const GECKO_429_SLEEP_MS = 15_000;

function short(addr) {
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function sol(lamports) {
  return lamports / 1_000_000_000;
}

function pct(from, to) {
  return ((to - from) / from) * 100;
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

function maxCumulativeDrawdownPctPoints(returns) {
  let equity = 0;
  let peak = 0;
  let drawdown = 0;
  for (const r of returns) {
    equity += r;
    peak = Math.max(peak, equity);
    drawdown = Math.min(drawdown, equity - peak);
  }
  return drawdown;
}

function summarizeReturns(name, returns) {
  const xs = returns.filter(Number.isFinite);
  const wins = xs.filter((r) => r > 0);
  const losses = xs.filter((r) => r < 0);
  const grossWin = wins.reduce((sum, r) => sum + r, 0);
  const grossLoss = Math.abs(losses.reduce((sum, r) => sum + r, 0));
  return {
    name,
    trades: xs.length,
    totalPct: xs.reduce((sum, r) => sum + r, 0),
    avgPct: avg(xs),
    medianPct: median(xs),
    p25Pct: quantile(xs, 0.25),
    p75Pct: quantile(xs, 0.75),
    winRatePct: xs.length ? (wins.length / xs.length) * 100 : null,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
    maxDrawdownPctPoints: maxCumulativeDrawdownPctPoints(xs),
  };
}

function fmtPct(value, digits = 1) {
  if (value == null || !Number.isFinite(value)) return "n/a";
  return `${value.toFixed(digits)}%`;
}

function fmtPctPoints(value, digits = 1) {
  if (value == null || !Number.isFinite(value)) return "n/a";
  return `${value.toFixed(digits)}pp`;
}

function fmtUsd(value) {
  if (value == null || !Number.isFinite(value)) return "n/a";
  return `$${Math.round(value).toLocaleString("en-US")}`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function cacheGet(key) {
  try {
    return JSON.parse(await fs.readFile(path.join(GECKO_CACHE_DIR, key), "utf8"));
  } catch {
    return null;
  }
}

async function cacheSet(key, value) {
  await fs.mkdir(GECKO_CACHE_DIR, { recursive: true });
  await fs.writeFile(path.join(GECKO_CACHE_DIR, key), JSON.stringify(value), "utf8");
}

async function fetchJsonCached(key, fetcher) {
  const cached = await cacheGet(key);
  if (cached) return cached;
  const value = await fetcher();
  await cacheSet(key, value);
  return value;
}

async function loadSolUsd() {
  const cached = JSON.parse(await fs.readFile(SOL_USD_CACHE, "utf8"));
  if (typeof cached.value !== "number" || cached.value <= 0) throw new Error("SOL/USD cache is invalid");
  return cached.value;
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
    let campaign = campaigns.get(swap.mint);
    if (!campaign) {
      if (swap.side !== "buy") continue;
      campaign = {
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
      campaigns.set(swap.mint, campaign);
    }

    if (swap.side === "buy") {
      campaign.spentLamports += swap.solLamports;
      campaign.boughtRaw += swap.tokenRaw;
      campaign.buys.push(swap);
    } else {
      campaign.receivedLamports += swap.solLamports;
      campaign.soldRaw += swap.tokenRaw;
      campaign.sells.push(swap);
      campaign.firstSell ??= swap;
    }
  }
  return [...campaigns.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

function sourceFillMarketCapUsd(campaign, solUsd) {
  if (!solUsd || campaign.firstBuy.tokenRaw <= 0) return null;
  const uiTokens = campaign.firstBuy.tokenRaw / 10 ** campaign.decimals;
  if (!Number.isFinite(uiTokens) || uiTokens <= 0) return null;
  return (sol(campaign.firstBuy.solLamports) * solUsd * SUPPLY_APPROX) / uiTokens;
}

function firstSellExitPct(campaign) {
  if (!campaign.firstSell || campaign.spentLamports <= 0 || campaign.boughtRaw <= 0 || campaign.firstSell.tokenRaw <= 0) {
    return null;
  }
  const avgEntryLamportsPerRaw = campaign.spentLamports / campaign.boughtRaw;
  const firstSellLamportsPerRaw = campaign.firstSell.solLamports / campaign.firstSell.tokenRaw;
  return pct(avgEntryLamportsPerRaw, firstSellLamportsPerRaw);
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

async function fetchGeckoOhlcv(poolAddress, entryTs) {
  const untilTs = entryTs + HORIZON_HOURS * 3600;
  return fetchJsonCached(`gecko-${poolAddress}-${entryTs}-${HORIZON_HOURS}.json`, async () => {
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
  return candles.find((c) => c.ts >= ts) ?? candles.find((c) => c.ts >= ts - 60) ?? null;
}

function analyzeRow(campaign, pair, candles, sourceFillMcUsd) {
  const entryCandle = candleAtOrAfter(candles, campaign.firstBuy.timestamp);
  const sourceExit = campaign.firstSell;
  const sourceExitPct = firstSellExitPct(campaign);
  if (!entryCandle || entryCandle.open <= 0 || !sourceExit || sourceExitPct == null) return null;

  const beforeSourceExit = candles.filter((c) => c.ts >= campaign.firstBuy.timestamp && c.ts <= sourceExit.timestamp);
  if (!beforeSourceExit.length) return null;

  const entryPrice = entryCandle.open;
  const candleEntryMcUsd = entryPrice * SUPPLY_APPROX;
  const hit = {};
  const hitTimeSec = {};
  for (const target of TARGETS) {
    const hitCandle = beforeSourceExit.find((c) => pct(entryPrice, c.high) >= target);
    hit[`tp${target}`] = Boolean(hitCandle);
    hitTimeSec[`tp${target}`] = hitCandle ? hitCandle.ts - campaign.firstBuy.timestamp : null;
  }

  const after = candles.filter((c) => c.ts >= campaign.firstBuy.timestamp);
  const high = after.length ? Math.max(...after.map((c) => c.high)) : null;
  const low = after.length ? Math.min(...after.map((c) => c.low)) : null;
  const mfePct24h = high == null ? null : pct(entryPrice, high);
  const maePct24h = low == null ? null : pct(entryPrice, low);

  return {
    mint: campaign.mint,
    symbol: pair.symbol || short(campaign.mint),
    dexId: pair.dexId,
    entryIso: new Date(campaign.firstBuy.timestamp * 1000).toISOString(),
    sourceExitIso: new Date(sourceExit.timestamp * 1000).toISOString(),
    holdSecondsToFirstSell: sourceExit.timestamp - campaign.firstBuy.timestamp,
    entrySol: sol(campaign.firstBuy.solLamports),
    totalSourceBuySol: sol(campaign.spentLamports),
    sourceFillMarketCapUsd: sourceFillMcUsd,
    candleEntryMarketCapUsd: candleEntryMcUsd,
    currentMarketCapUsd: pair.currentMarketCapUsd,
    currentLiquidityUsd: pair.liquidityUsd,
    sourceExitPct,
    mfePct24h,
    maePct24h,
    hit,
    hitTimeSec,
    strategyReturns: {
      mirrorCyaFirstSell: sourceExitPct,
      tp30Sell100: hit.tp30 ? 30 : sourceExitPct,
      tp50Sell100: hit.tp50 ? 50 : sourceExitPct,
      tp75Sell100: hit.tp75 ? 75 : sourceExitPct,
      tp100Sell100: hit.tp100 ? 100 : sourceExitPct,
    },
    firstBuySignature: campaign.firstBuy.signature,
    firstSellSignature: sourceExit.signature,
  };
}

function markdown(output) {
  const targetRows = TARGETS.map((target) => {
    const hits = output.rows.filter((row) => row.hit[`tp${target}`]);
    return `| +${target}% | ${hits.length}/${output.rows.length} | ${fmtPct((hits.length / Math.max(1, output.rows.length)) * 100)} | ${median(hits.map((row) => row.hitTimeSec[`tp${target}`] / 60))?.toFixed(1) ?? "n/a"} |`;
  }).join("\n");

  const strategyRows = output.strategySummaries.map((s) => {
    const pf = s.profitFactor === Infinity ? "inf" : s.profitFactor.toFixed(2);
    return `| ${s.name} | ${s.trades} | ${fmtPct(s.avgPct)} | ${fmtPct(s.medianPct)} | ${fmtPct(s.winRatePct)} | ${pf} | ${fmtPctPoints(s.totalPct)} | ${fmtPctPoints(s.maxDrawdownPctPoints)} |`;
  }).join("\n");

  const tradeRows = output.rows.map((row) => {
    return `| ${row.symbol} (\`${short(row.mint)}\`) | ${fmtUsd(row.sourceFillMarketCapUsd)} | ${row.entrySol.toFixed(2)} | ${fmtPct(row.sourceExitPct)} | ${TARGETS.map((target) => row.hit[`tp${target}`] ? "Y" : "N").join(" / ")} | ${Math.round(row.holdSecondsToFirstSell / 60)} |`;
  }).join("\n");

  return `# CyA MC >= $50k Target vs Source Exit Analysis

Source wallet: \`${WALLET}\`

Assumptions:
- One row per token campaign, deduped by mint.
- Entry MC filter is source-fill implied MC: first-buy SOL spent x cached SOL/USD / first-buy tokens x 1B supply.
- Source exit is the first CyA sell after the first buy.
- TP strategies sell 100% at the target only if the target is hit before that first CyA sell; otherwise they fall back to the same first-sell source exit.

## Coverage

- Wallet tx cache: ${output.coverage.walletTxs.toLocaleString("en-US")} txs
- Swaps reconstructed: ${output.coverage.swaps.toLocaleString("en-US")}
- Token campaigns: ${output.coverage.campaigns.toLocaleString("en-US")}
- Source-fill MC >= ${fmtUsd(output.thresholdUsd)} candidates: ${output.coverage.candidates.toLocaleString("en-US")}
- Measured rows: ${output.rows.length.toLocaleString("en-US")}
- Skipped candidates: ${output.skips.length.toLocaleString("en-US")}
- SOL/USD used: $${output.solUsd}
- Entry MC median: ${fmtUsd(output.entryMc.median)}
- Entry MC p25/p75: ${fmtUsd(output.entryMc.p25)} / ${fmtUsd(output.entryMc.p75)}

## Target Hit Before Source Exit

| Target | Hits | Hit Rate | Median Hit Minutes |
|---|---:|---:|---:|
${targetRows}

## Exit Comparison

Returns are per-trade ROI. Total and cumulative DD are percentage-point sums, not bankroll percentages.

| Strategy | Trades | Avg | Median | Win Rate | PF | Total | Cumulative DD |
|---|---:|---:|---:|---:|---:|---:|---:|
${strategyRows}

## Trades

| Token | Source-Fill Entry MC | Entry SOL | Source Exit | Hit +30/+50/+75/+100 | Hold Min |
|---|---:|---:|---:|---|---:|
${tradeRows}

## Skips

${output.skips.map((skip) => `- ${short(skip.mint)}: ${skip.reason}`).join("\n")}
`;
}

async function main() {
  const [txs, solUsd] = await Promise.all([
    fs.readFile(WALLET_CACHE, "utf8").then(JSON.parse),
    loadSolUsd(),
  ]);
  const swaps = txs.map((tx) => extractSwap(tx, WALLET)).filter(Boolean);
  const campaigns = buildCampaigns(swaps);
  const candidates = campaigns
    .map((campaign) => ({ campaign, sourceFillMcUsd: sourceFillMarketCapUsd(campaign, solUsd) }))
    .filter((row) => (row.sourceFillMcUsd ?? 0) >= MIN_ENTRY_MC_USD && row.campaign.firstSell);

  const rows = [];
  const skips = [];
  for (const [index, candidate] of candidates.entries()) {
    const campaign = candidate.campaign;
    try {
      const pair = await getTokenPairInfo(campaign.mint);
      if (!pair?.pairAddress) {
        skips.push({ mint: campaign.mint, reason: "no DexScreener pair" });
        continue;
      }
      const candles = await fetchGeckoOhlcv(pair.pairAddress, campaign.firstBuy.timestamp);
      const row = analyzeRow(campaign, pair, candles, candidate.sourceFillMcUsd);
      if (!row) {
        skips.push({ mint: campaign.mint, reason: "missing entry/source-exit candle data" });
        continue;
      }
      rows.push(row);
      console.log(`${index + 1}/${candidates.length} ${row.symbol} measured`);
    } catch (err) {
      skips.push({ mint: campaign.mint, reason: err instanceof Error ? err.message : String(err) });
      console.warn(`${index + 1}/${candidates.length} ${short(campaign.mint)} skipped: ${skips.at(-1).reason}`);
    }
    await sleep(GECKO_CANDIDATE_SLEEP_MS);
  }

  rows.sort((a, b) => b.sourceFillMarketCapUsd - a.sourceFillMarketCapUsd);
  const strategySummaries = [
    summarizeReturns("mirror CyA first sell", rows.map((row) => row.strategyReturns.mirrorCyaFirstSell)),
    summarizeReturns("TP30 sell 100%", rows.map((row) => row.strategyReturns.tp30Sell100)),
    summarizeReturns("TP50 sell 100%", rows.map((row) => row.strategyReturns.tp50Sell100)),
    summarizeReturns("TP75 sell 100%", rows.map((row) => row.strategyReturns.tp75Sell100)),
    summarizeReturns("TP100 sell 100%", rows.map((row) => row.strategyReturns.tp100Sell100)),
  ];

  const output = {
    generatedAt: new Date().toISOString(),
    wallet: WALLET,
    thresholdUsd: MIN_ENTRY_MC_USD,
    horizonHours: HORIZON_HOURS,
    solUsd,
    assumptions: {
      dedupe: "one campaign per mint",
      entryMc: "source-fill implied market cap",
      sourceExit: "first CyA sell after first buy",
      tpFallback: "if target is not hit before first CyA sell, exit at first CyA sell",
    },
    coverage: {
      walletTxs: txs.length,
      swaps: swaps.length,
      campaigns: campaigns.length,
      candidates: candidates.length,
    },
    targetHitSummary: TARGETS.map((target) => {
      const hits = rows.filter((row) => row.hit[`tp${target}`]);
      return {
        targetPct: target,
        hits: hits.length,
        trades: rows.length,
        hitRatePct: rows.length ? (hits.length / rows.length) * 100 : null,
        medianHitMinutes: median(hits.map((row) => row.hitTimeSec[`tp${target}`] / 60)),
      };
    }),
    strategySummaries,
    entryMc: {
      median: median(rows.map((row) => row.sourceFillMarketCapUsd)),
      p25: quantile(rows.map((row) => row.sourceFillMarketCapUsd), 0.25),
      p75: quantile(rows.map((row) => row.sourceFillMarketCapUsd), 0.75),
    },
    rows,
    skips,
  };

  await fs.writeFile(OUT_JSON, JSON.stringify(output, null, 2));
  await fs.writeFile(OUT_MD, markdown(output));
  console.log(JSON.stringify({
    rows: rows.length,
    coverage: output.coverage,
    skips: skips.length,
    targetHitSummary: output.targetHitSummary,
    strategySummaries,
    outputJson: OUT_JSON,
    outputMd: OUT_MD,
  }, null, 2));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
