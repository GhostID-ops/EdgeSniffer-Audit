import "dotenv/config";
import fs from "node:fs/promises";
import path from "node:path";

const WALLET = process.argv[2] ?? "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o";
const MAX_TXS = Number(process.argv[3] ?? process.env.CYA_BAGS_MAX_TXS ?? "12000");
const HORIZON_HOURS = Number(process.argv[4] ?? process.env.CYA_BAGS_HORIZON_HOURS ?? "24");
const MIN_ENTRY_MC_USD = Number(process.argv[5] ?? process.env.CYA_BAGS_MIN_ENTRY_MC_USD ?? "0");
const HELIUS_API_KEY = process.env.HELIUS_API_KEY ?? "";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const GECKO_BASE = "https://api.geckoterminal.com/api/v2";
const SOL_USD = Number(process.env.LOCAL_PUMPFUN_SOL_PRICE_USD ?? process.env.SOL_PRICE_USD ?? "150");
const SUPPLY_APPROX = Number(process.env.PREBUY_INFERRED_TOKEN_SUPPLY ?? "1000000000");
const TARGETS = [30, 50, 75, 100, 200];
const ENRICH_PATHS = String(process.env.CYA_BAGS_ENRICH_PATHS ?? "true").toLowerCase() !== "false";
const TRAILING_STEPS = parseTrailingSteps(
  process.env.CYA_BAGS_TRAILING_STEPS ?? "50:25,100:75,200:125,300:200,500:350,600:400,800:500,1200:1000"
);
const CACHE_DIR = path.resolve(process.cwd(), "data", "cya-bags-cache");
const OUT_JSON = path.resolve(process.cwd(), "data", "cya-bags-analysis.json");
const OUT_MD = path.resolve(process.cwd(), "data", "cya-bags-analysis.md");
const PAGE_SLEEP_MS = Number(process.env.CYA_BAGS_PAGE_SLEEP_MS ?? "650");
const TOKEN_SLEEP_MS = Number(process.env.CYA_BAGS_TOKEN_SLEEP_MS ?? "850");
const RATE_LIMIT_SLEEP_MS = Number(process.env.CYA_BAGS_429_SLEEP_MS ?? "2500");

if (!HELIUS_API_KEY) {
  console.error("HELIUS_API_KEY is not set in .env.");
  process.exit(1);
}

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

function fmtSol(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)} SOL`;
}

function fmtUsd(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  return `$${Math.round(value).toLocaleString("en-US")}`;
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
  for (let attempt = 1; attempt <= 5; attempt++) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(options.timeoutMs ?? 20_000) });
      if (res.status === 429 || res.status >= 500) {
        const text = await res.text();
        lastErr = new Error(`HTTP ${res.status}: ${text.slice(0, 180)}`);
        await sleep(attempt * RATE_LIMIT_SLEEP_MS);
        continue;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 180)}`);
      return await res.json();
    } catch (err) {
      lastErr = err;
      await sleep(attempt * 1000);
    }
  }
  throw lastErr;
}

async function fetchWalletTxs(wallet, maxTxs) {
  const cacheKey = `wallet-${wallet}-${maxTxs}.json`;
  const cached = await readCache(cacheKey);
  if (cached) {
    console.log(`Using cached wallet txs: ${cached.length}`);
    return cached;
  }

  const all = [];
  let before = null;
  while (all.length < maxTxs) {
    const limit = Math.min(100, maxTxs - all.length);
    const url =
      `https://api.helius.xyz/v0/addresses/${wallet}/transactions?api-key=${HELIUS_API_KEY}` +
      `&limit=${limit}${before ? `&before=${before}` : ""}`;
    const batch = await fetchJson(url, { timeoutMs: 25_000 });
    if (!Array.isArray(batch) || batch.length === 0) break;
    all.push(...batch);
    before = batch.at(-1)?.signature;
    const oldest = Math.min(...batch.map((tx) => tx.timestamp ?? Infinity));
    console.log(`Helius page ${Math.ceil(all.length / 100)}: total=${all.length}, oldest=${new Date(oldest * 1000).toISOString()}`);
    await sleep(PAGE_SLEEP_MS);
  }

  const deduped = dedupeTxs(all);
  await writeCache(cacheKey, deduped);
  return deduped;
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
      campaign.receivedLamports += swap.solLamports;
      campaign.soldRaw += swap.tokenRaw;
      campaign.firstSell ??= swap;
    }
  }
  return [...byMint.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

function sourceFillMarketCapUsd(campaign) {
  if (!SOL_USD || campaign.firstBuy.tokenRaw <= 0) return null;
  const uiTokens = campaign.firstBuy.tokenRaw / 10 ** campaign.decimals;
  if (!Number.isFinite(uiTokens) || uiTokens <= 0) return null;
  return (sol(campaign.firstBuy.solLamports) * SOL_USD * SUPPLY_APPROX) / uiTokens;
}

function campaignPnl(campaign) {
  if (campaign.spentLamports <= 0 || campaign.receivedLamports <= 0 || campaign.boughtRaw <= 0 || campaign.soldRaw <= 0) {
    return null;
  }
  const soldFraction = Math.min(campaign.soldRaw, campaign.boughtRaw) / campaign.boughtRaw;
  const costBasis = campaign.spentLamports * soldFraction;
  const pnl = campaign.receivedLamports - costBasis;
  return {
    costBasisSol: sol(costBasis),
    pnlSol: sol(pnl),
    roiPct: costBasis > 0 ? (pnl / costBasis) * 100 : null,
  };
}

function sourceFirstSellPct(campaign) {
  if (!campaign.firstSell || campaign.spentLamports <= 0 || campaign.boughtRaw <= 0 || campaign.firstSell.tokenRaw <= 0) return null;
  const avgEntry = campaign.spentLamports / campaign.boughtRaw;
  const firstSell = campaign.firstSell.solLamports / campaign.firstSell.tokenRaw;
  return ((firstSell - avgEntry) / avgEntry) * 100;
}

async function getTokenPairInfos(mint) {
  const key = `pairs-${mint}.json`;
  const cached = await readCache(key);
  if (Array.isArray(cached)) return cached;

  const json = await fetchJson(`https://api.dexscreener.com/latest/dex/tokens/${mint}`, { timeoutMs: 12_000 });
  const pairs = (json.pairs ?? []).filter((pair) => pair.chainId === "solana" && pair.pairAddress);
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
  await writeCache(key, out);
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
    const json = await fetchJson(url, { timeoutMs: 18_000 });
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
  const pathRows = candles.filter((candle) => candle.ts >= entry.ts && candle.ts <= endTs);
  if (!pathRows.length) return null;
  const entryPrice = entry.open || entry.close;
  const mfePct = Math.max(...pathRows.map((candle) => roiFromPrice(entryPrice, candle.high)).filter(Number.isFinite));
  const maePct = Math.min(...pathRows.map((candle) => roiFromPrice(entryPrice, candle.low)).filter(Number.isFinite));

  const hit = {};
  const hitTimeSec = {};
  for (const target of TARGETS) {
    const targetCandle = pathRows.find((candle) => (roiFromPrice(entryPrice, candle.high) ?? -Infinity) >= target);
    hit[`tp${target}`] = Boolean(targetCandle);
    hitTimeSec[`tp${target}`] = targetCandle ? targetCandle.ts - campaign.firstBuy.timestamp : null;
  }

  let peak = -Infinity;
  let activeFloor = null;
  let trailingExit = null;
  for (const candle of pathRows) {
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
    .map((row) => `| ${row.symbol || short(row.mint)} | \`${short(row.mint)}\` | ${row.entryIso} | ${fmtUsd(row.sourceFillMarketCapUsd)} | ${fmtSol(row.sourceBuySol)} | ${fmtSol(row.realizedPnlSol)} | ${fmtPct(row.realizedRoiPct)} | ${fmtPct(row.firstSellRoiPct)} | ${fmtPct(row.mfePct)} | ${fmtPct(row.maePct)} | ${row.trailingExit ? `${fmtPct(row.trailingExit.roiPct)} @ ${Math.round(row.trailingExit.timeSec / 60)}m` : "-"} |`)
    .join("\n");
  const campaignRows = output.campaignRows
    .map((row) => `| ${short(row.mint)} | ${row.entryIso} | ${fmtUsd(row.sourceFillMarketCapUsd)} | ${fmtSol(row.sourceBuySol)} | ${row.buys}/${row.sells} | ${fmtSol(row.realizedPnlSol)} | ${fmtPct(row.realizedRoiPct)} | ${fmtPct(row.firstSellRoiPct)} | ${row.holdToFirstSellMin == null ? "-" : row.holdToFirstSellMin.toFixed(1)} |`)
    .join("\n");

  return `# CyA BAGS-Only Trade Analysis

Generated: ${output.generatedAt}

Assumptions:
- Fresh Helius wallet scan up to ${output.maxTxs.toLocaleString("en-US")} latest txs.
- BAGS filter is \`mint.endsWith("BAGS")\`.
- Entry MC is inferred from CyA's first fill, SOL/USD=${output.solUsd}, supply=${output.supplyApprox.toLocaleString("en-US")}.
- Realized PnL is campaign-level cost basis for sold tokens only; open remainders are not marked as realized.
- MFE/MAE and TP/trailing rows use GeckoTerminal 1-minute candles on the best DexScreener liquidity pair, capped at ${output.horizonHours}h or CyA's first sell.
- Same-candle high/low ordering is unknown, so trailing exits are directionally useful, not exact fill proof.

## Coverage

- Wallet txs fetched: ${output.coverage.walletTxs}
- Swaps reconstructed: ${output.coverage.swaps}
- Total campaigns: ${output.coverage.totalCampaigns}
- BAGS campaigns: ${output.coverage.bagsCampaigns}
- BAGS campaigns after MC filter (${fmtUsd(output.minEntryMcUsd)}): ${output.coverage.filteredCampaigns}
- Measured candle rows: ${output.rows.length}
- Skips: ${output.skips.length}
- Gecko enrichment: ${output.enrichPaths ? "enabled" : "disabled"}
- Date span: ${output.coverage.firstEntryIso ?? "-"} through ${output.coverage.lastEntryIso ?? "-"}

## Realized BAGS Stats

- Closed campaigns: ${output.realized.closed}
- Wins: ${output.realized.wins}/${output.realized.closed}
- Realized PnL: ${fmtSol(output.realized.totalPnlSol)}
- Avg/median ROI: ${fmtPct(output.realized.avgRoiPct)} / ${fmtPct(output.realized.medianRoiPct)}
- Profit factor: ${output.realized.profitFactor === Infinity ? "inf" : output.realized.profitFactor.toFixed(2)}
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

## All BAGS Campaigns

| Mint | Entry UTC | Entry MC | CyA Buy | Buys/Sells | Realized PnL | Realized ROI | First Sell ROI | Hold Min |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
${campaignRows}

## Candle-Measured Trades

| Token | Mint | Entry UTC | Entry MC | CyA Buy | Realized PnL | Realized ROI | First Sell ROI | MFE | MAE | Trailing Exit |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
${tradeRows}

## Skips

${output.skips.map((skip) => `- \`${short(skip.mint)}\`: ${skip.reason}`).join("\n") || "None"}
`;
}

async function main() {
  const txs = await fetchWalletTxs(WALLET, MAX_TXS);
  const swaps = txs.map((tx) => extractSwap(tx, WALLET)).filter(Boolean);
  const campaigns = buildCampaigns(swaps);
  const bagsCampaigns = campaigns.filter((campaign) => campaign.mint.endsWith("BAGS"));
  const candidates = bagsCampaigns
    .map((campaign) => ({
      campaign,
      sourceFillMarketCapUsd: sourceFillMarketCapUsd(campaign),
      realized: campaignPnl(campaign),
      firstSellRoiPct: sourceFirstSellPct(campaign),
    }))
    .filter((row) => (row.sourceFillMarketCapUsd ?? 0) >= MIN_ENTRY_MC_USD);

  const campaignRows = candidates.map(({ campaign, sourceFillMarketCapUsd, realized, firstSellRoiPct }) => ({
    mint: campaign.mint,
    entryIso: new Date(campaign.firstBuy.timestamp * 1000).toISOString(),
    sourceFillMarketCapUsd,
    sourceBuySol: sol(campaign.firstBuy.solLamports),
    totalBuySol: sol(campaign.spentLamports),
    totalSellSol: sol(campaign.receivedLamports),
    realizedPnlSol: realized?.pnlSol ?? null,
    realizedRoiPct: realized?.roiPct ?? null,
    firstSellRoiPct,
    holdToFirstSellMin: campaign.firstSell ? (campaign.firstSell.timestamp - campaign.firstBuy.timestamp) / 60 : null,
    buys: campaign.buys.length,
    sells: campaign.sells.length,
    firstBuySignature: campaign.firstBuy.signature,
    firstSellSignature: campaign.firstSell?.signature ?? null,
  }));

  const rows = [];
  const skips = [];
  for (const [index, candidate] of (ENRICH_PATHS ? candidates : []).entries()) {
    const { campaign } = candidate;
    try {
      const pairs = await getTokenPairInfos(campaign.mint);
      if (!pairs.length) {
        skips.push({ mint: campaign.mint, reason: "no DexScreener pair" });
        continue;
      }

      let pair = null;
      let candles = null;
      let pathResult = null;
      const failures = [];
      for (const candidatePair of pairs) {
        try {
          const candidateCandles = await fetchGeckoOhlcv(candidatePair.pairAddress, campaign.firstBuy.timestamp);
          const candidatePath = analyzePath(campaign, candidateCandles);
          if (candidatePath) {
            pair = candidatePair;
            candles = candidateCandles;
            pathResult = candidatePath;
            break;
          }
          failures.push(`${candidatePair.dexId ?? "unknown"}:${short(candidatePair.pairAddress)} no candles`);
        } catch (err) {
          failures.push(`${candidatePair.dexId ?? "unknown"}:${short(candidatePair.pairAddress)} ${err instanceof Error ? err.message : String(err)}`);
        }
        await sleep(350);
      }
      if (!pathResult) {
        skips.push({ mint: campaign.mint, reason: `missing Gecko path: ${failures.slice(0, 4).join("; ")}` });
        continue;
      }

      const fallbackExit =
        candidate.firstSellRoiPct ??
        roiFromPrice(pathResult.entryPrice, candles.at(-1)?.close ?? pathResult.entryPrice) ??
        0;
      const strategyReturns = {
        mirrorCyaFirstSell: fallbackExit,
        tp30Sell100: pathResult.hit.tp30 ? 30 : fallbackExit,
        tp50Sell100: pathResult.hit.tp50 ? 50 : fallbackExit,
        tp75Sell100: pathResult.hit.tp75 ? 75 : fallbackExit,
        tp100Sell100: pathResult.hit.tp100 ? 100 : fallbackExit,
        tp200Sell100: pathResult.hit.tp200 ? 200 : fallbackExit,
        trailingCustom: pathResult.trailingExit?.roiPct ?? fallbackExit,
      };

      rows.push({
        mint: campaign.mint,
        symbol: pair.symbol,
        entryIso: new Date(campaign.firstBuy.timestamp * 1000).toISOString(),
        sourceFillMarketCapUsd: candidate.sourceFillMarketCapUsd,
        sourceBuySol: sol(campaign.firstBuy.solLamports),
        totalBuySol: sol(campaign.spentLamports),
        totalSellSol: sol(campaign.receivedLamports),
        realizedPnlSol: candidate.realized?.pnlSol ?? null,
        realizedRoiPct: candidate.realized?.roiPct ?? null,
        firstSellRoiPct: candidate.firstSellRoiPct,
        holdToFirstSellMin: campaign.firstSell ? (campaign.firstSell.timestamp - campaign.firstBuy.timestamp) / 60 : null,
        buys: campaign.buys.length,
        sells: campaign.sells.length,
        currentMarketCapUsd: pair.currentMarketCapUsd,
        liquidityUsd: pair.liquidityUsd,
        dexId: pair.dexId,
        pairAddress: pair.pairAddress,
        dexscreenerUrl: pair.url,
        mfePct: pathResult.mfePct,
        maePct: pathResult.maePct,
        hit: pathResult.hit,
        hitTimeSec: pathResult.hitTimeSec,
        trailingExit: pathResult.trailingExit,
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
    await sleep(TOKEN_SLEEP_MS);
  }

  const realizedRows = candidates.map((row) => row.realized).filter(Boolean);
  const wins = realizedRows.filter((row) => row.pnlSol > 0);
  const losses = realizedRows.filter((row) => row.pnlSol < 0);
  const grossWin = wins.reduce((sum, row) => sum + row.pnlSol, 0);
  const grossLoss = Math.abs(losses.reduce((sum, row) => sum + row.pnlSol, 0));
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
  const strategySummaries = [
    summarizeReturns("mirror CyA first sell", rows.map((row) => row.strategyReturns.mirrorCyaFirstSell)),
    summarizeReturns("TP30 sell 100%", rows.map((row) => row.strategyReturns.tp30Sell100)),
    summarizeReturns("TP50 sell 100%", rows.map((row) => row.strategyReturns.tp50Sell100)),
    summarizeReturns("TP75 sell 100%", rows.map((row) => row.strategyReturns.tp75Sell100)),
    summarizeReturns("TP100 sell 100%", rows.map((row) => row.strategyReturns.tp100Sell100)),
    summarizeReturns("TP200 sell 100%", rows.map((row) => row.strategyReturns.tp200Sell100)),
    summarizeReturns("custom trailing", rows.map((row) => row.strategyReturns.trailingCustom)),
  ];

  const sortedRows = rows.sort((a, b) => (b.sourceFillMarketCapUsd ?? 0) - (a.sourceFillMarketCapUsd ?? 0));
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: WALLET,
    maxTxs: MAX_TXS,
    minEntryMcUsd: MIN_ENTRY_MC_USD,
    horizonHours: HORIZON_HOURS,
    enrichPaths: ENRICH_PATHS,
    solUsd: SOL_USD,
    supplyApprox: SUPPLY_APPROX,
    trailingSteps: TRAILING_STEPS,
    coverage: {
      walletTxs: txs.length,
      swaps: swaps.length,
      totalCampaigns: campaigns.length,
      bagsCampaigns: bagsCampaigns.length,
      filteredCampaigns: candidates.length,
      firstEntryIso: bagsCampaigns.at(-1) ? new Date(bagsCampaigns.at(-1).firstBuy.timestamp * 1000).toISOString() : null,
      lastEntryIso: bagsCampaigns[0] ? new Date(bagsCampaigns[0].firstBuy.timestamp * 1000).toISOString() : null,
    },
    realized: {
      closed: realizedRows.length,
      wins: wins.length,
      totalPnlSol: realizedRows.reduce((sum, row) => sum + row.pnlSol, 0),
      avgRoiPct: avg(realizedRows.map((row) => row.roiPct)),
      medianRoiPct: median(realizedRows.map((row) => row.roiPct)),
      profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
    },
    entryMc: {
      median: median((sortedRows.length ? sortedRows : campaignRows).map((row) => row.sourceFillMarketCapUsd)),
      p25: quantile((sortedRows.length ? sortedRows : campaignRows).map((row) => row.sourceFillMarketCapUsd), 0.25),
      p75: quantile((sortedRows.length ? sortedRows : campaignRows).map((row) => row.sourceFillMarketCapUsd), 0.75),
    },
    path: {
      medianMfePct: median(sortedRows.map((row) => row.mfePct)),
      medianMaePct: median(sortedRows.map((row) => row.maePct)),
    },
    targetHitSummary,
    strategySummaries,
    campaignRows: campaignRows.sort((a, b) => (b.sourceFillMarketCapUsd ?? 0) - (a.sourceFillMarketCapUsd ?? 0)),
    rows: sortedRows,
    skips,
  };

  await fs.writeFile(OUT_JSON, JSON.stringify(output, null, 2), "utf8");
  await fs.writeFile(OUT_MD, markdown(output), "utf8");
  console.log(JSON.stringify({
    coverage: output.coverage,
    realized: output.realized,
    measuredRows: output.rows.length,
    skips: output.skips.length,
    outputJson: OUT_JSON,
    outputMd: OUT_MD,
  }, null, 2));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.stack ?? err.message : String(err));
  process.exit(1);
});
