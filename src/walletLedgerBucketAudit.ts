import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { heliusAddressTransactionsUrl } from "./config.js";

const WSOL = "So11111111111111111111111111111111111111112";
const USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const SUPPLY = Number(process.env.PREBUY_INFERRED_TOKEN_SUPPLY ?? "1000000000");
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-ledger-bucket-cache");
const SHARED_CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-quant-audit-cache");

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

type Fill = {
  signature: string;
  timestamp: number;
  side: "buy" | "sell";
  mint: string;
  tokenRaw: number;
  decimals: number;
  quoteLamports: number;
};

type Campaign = {
  mint: string;
  decimals: number;
  firstBuy: Fill;
  buys: Fill[];
  sells: Fill[];
  spentLamports: number;
  boughtRaw: number;
  receivedLamports: number;
  soldRaw: number;
};

type Row = {
  mint: string;
  route: "pump" | "bags" | "other";
  entryTs: number;
  entryIso: string;
  entrySolUsd: number;
  firstBuySol: number;
  entryTokens: number;
  entryMarketCapUsd: number;
  buys: number;
  sells: number;
  sourceExitRoiPct: number;
  realizedPnlSol: number;
  holdMinutes: number;
};

type Bucket = {
  name: string;
  n: number;
  winRatePct: number;
  avgRoiPct: number | null;
  medianRoiPct: number | null;
  /** Equal-sized-copy profit factor: sum of positive ROI points / absolute negative ROI points. */
  profitFactor: number | null;
  /** Source-capital-weighted profit factor in realized SOL. */
  sourceCapitalProfitFactor: number | null;
  totalPnlSol: number;
  medianEntryMcUsd: number | null;
  medianHoldMinutes: number | null;
};

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const sol = (lamports: number) => lamports / 1_000_000_000;

function avg(values: number[]): number | null {
  const xs = values.filter(Number.isFinite);
  return xs.length ? xs.reduce((sum, value) => sum + value, 0) / xs.length : null;
}

function median(values: number[]): number | null {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid]! : (xs[mid - 1]! + xs[mid]!) / 2;
}

function fmtPct(value: number | null): string {
  return value == null ? "-" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtUsd(value: number | null): string {
  return value == null ? "-" : `$${Math.round(value).toLocaleString("en-US")}`;
}

async function readJson<T>(file: string): Promise<T | null> {
  try {
    return JSON.parse(await readFile(file, "utf8")) as T;
  } catch {
    return null;
  }
}

async function fetchAddressTxs(address: string, limit: number, before?: string): Promise<HeliusTx[]> {
  const url = heliusAddressTransactionsUrl(address, before, limit);
  if (!url) throw new Error("HELIUS_API_KEY is not set");
  const response = await fetch(url, { signal: AbortSignal.timeout(20_000) });
  if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 180)}`);
  return (await response.json()) as HeliusTx[];
}

async function fetchWalletTxs(wallet: string, maxTxs: number): Promise<HeliusTx[]> {
  const key = `wallet-${wallet}-${maxTxs}.json`;
  const ownPath = path.join(CACHE_DIR, key);
  const cached =
    (await readJson<HeliusTx[]>(ownPath)) ??
    (await readJson<HeliusTx[]>(path.join(SHARED_CACHE_DIR, key)));
  if (cached) return cached;

  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(wallet, Math.min(100, maxTxs - all.length), before);
    if (!batch.length) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    if (all.length % 1_000 === 0) console.error(`Fetched ${all.length}/${maxTxs} wallet transactions...`);
    await sleep(650);
  }
  await mkdir(CACHE_DIR, { recursive: true });
  await writeFile(ownPath, JSON.stringify(all), "utf8");
  return all;
}

function extractFill(tx: HeliusTx, wallet: string): Fill | null {
  let nativeNet = 0;
  let wsolNet = 0;
  const tokens = new Map<string, { raw: number; decimals: number }>();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet || !change.mint) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      if (change.mint === WSOL) {
        wsolNet += raw;
        continue;
      }
      if (change.mint === USDC) continue;
      const current = tokens.get(change.mint) ?? {
        raw: 0,
        decimals: change.rawTokenAmount?.decimals ?? 0,
      };
      current.raw += raw;
      current.decimals = change.rawTokenAmount?.decimals ?? current.decimals;
      tokens.set(change.mint, current);
    }
  }

  const token = [...tokens.entries()].sort((a, b) => Math.abs(b[1].raw) - Math.abs(a[1].raw))[0];
  if (!token) return null;
  const [mint, delta] = token;
  const quoteNet = nativeNet + wsolNet;
  if (delta.raw > 0 && quoteNet < 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "buy",
      mint,
      tokenRaw: delta.raw,
      decimals: delta.decimals,
      quoteLamports: Math.abs(quoteNet),
    };
  }
  if (delta.raw < 0 && quoteNet > 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      side: "sell",
      mint,
      tokenRaw: Math.abs(delta.raw),
      decimals: delta.decimals,
      quoteLamports: quoteNet,
    };
  }
  return null;
}

export function buildCampaigns(fills: Fill[]): Campaign[] {
  const campaigns: Campaign[] = [];
  const active = new Map<string, Campaign>();
  for (const fill of fills.sort((a, b) => a.timestamp - b.timestamp)) {
    let campaign = active.get(fill.mint);
    if (fill.side === "buy" && !campaign) {
      campaign = {
        mint: fill.mint,
        decimals: fill.decimals,
        firstBuy: fill,
        buys: [],
        sells: [],
        spentLamports: 0,
        boughtRaw: 0,
        receivedLamports: 0,
        soldRaw: 0,
      };
      campaigns.push(campaign);
      active.set(fill.mint, campaign);
    }
    if (!campaign) continue;
    if (fill.side === "buy") {
      campaign.buys.push(fill);
      campaign.spentLamports += fill.quoteLamports;
      campaign.boughtRaw += fill.tokenRaw;
    } else {
      campaign.sells.push(fill);
      campaign.receivedLamports += fill.quoteLamports;
      campaign.soldRaw += fill.tokenRaw;
      if (campaign.soldRaw >= campaign.boughtRaw) {
        active.delete(fill.mint);
      }
    }
  }
  return campaigns;
}

type PricePoint = [number, number];

async function loadSolPrices(fromTs: number, toTs: number): Promise<{ prices: PricePoint[]; source: string }> {
  const fromDay = Math.floor(fromTs / 86_400) * 86_400;
  const toDay = Math.ceil(toTs / 86_400) * 86_400;
  const cachePath = path.join(CACHE_DIR, `sol-usd-${fromDay}-${toDay}.json`);
  const cached = await readJson<PricePoint[]>(cachePath);
  if (cached?.length) return { prices: cached, source: "CoinGecko range cache" };

  try {
    const params = new URLSearchParams({
      vs_currency: "usd",
      from: String(fromDay),
      to: String(toDay),
    });
    const response = await fetch(
      `https://api.coingecko.com/api/v3/coins/solana/market_chart/range?${params}`,
      { signal: AbortSignal.timeout(20_000) }
    );
    if (!response.ok) throw new Error(`CoinGecko ${response.status}`);
    const json = (await response.json()) as { prices?: Array<[number, number]> };
    const prices = (json.prices ?? [])
      .map(([ms, value]) => [Math.floor(ms / 1000), value] as PricePoint)
      .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]) && point[1] > 0);
    if (!prices.length) throw new Error("CoinGecko returned no SOL prices");
    await mkdir(CACHE_DIR, { recursive: true });
    await writeFile(cachePath, JSON.stringify(prices), "utf8");
    return { prices, source: "CoinGecko historical range" };
  } catch (err) {
    const fallback = Number(
      process.env.LOCAL_PUMPFUN_SOL_PRICE_USD ?? process.env.SOL_PRICE_USD ?? "0"
    );
    if (!Number.isFinite(fallback) || fallback <= 0) {
      throw new Error(
        `Historical SOL/USD unavailable (${err instanceof Error ? err.message : String(err)}) and no local fallback is configured`
      );
    }
    return { prices: [[fromTs, fallback]], source: "configured current SOL/USD fallback" };
  }
}

function nearestPrice(prices: PricePoint[], timestamp: number): number {
  let lo = 0;
  let hi = prices.length - 1;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (prices[mid]![0] < timestamp) lo = mid + 1;
    else hi = mid;
  }
  const after = prices[lo]!;
  const before = prices[Math.max(0, lo - 1)]!;
  return Math.abs(after[0] - timestamp) < Math.abs(before[0] - timestamp) ? after[1] : before[1];
}

function route(mint: string): Row["route"] {
  if (mint.endsWith("pump")) return "pump";
  if (mint.endsWith("BAGS")) return "bags";
  return "other";
}

function buildRows(campaigns: Campaign[], prices: PricePoint[]): Row[] {
  const rows: Row[] = [];
  for (const campaign of campaigns) {
    if (
      campaign.spentLamports <= 0 ||
      campaign.boughtRaw <= 0 ||
      campaign.receivedLamports <= 0 ||
      campaign.soldRaw <= 0
    ) continue;
    const entryTokens = campaign.firstBuy.tokenRaw / 10 ** campaign.firstBuy.decimals;
    if (!Number.isFinite(entryTokens) || entryTokens <= 0) continue;
    const entrySolUsd = nearestPrice(prices, campaign.firstBuy.timestamp);
    const entryMarketCapUsd =
      ((sol(campaign.firstBuy.quoteLamports) * entrySolUsd) / entryTokens) * SUPPLY;
    if (!Number.isFinite(entryMarketCapUsd) || entryMarketCapUsd <= 0) continue;

    const costBasisSold =
      campaign.spentLamports *
      (Math.min(campaign.soldRaw, campaign.boughtRaw) / campaign.boughtRaw);
    if (costBasisSold <= 0) continue;
    const pnlLamports = campaign.receivedLamports - costBasisSold;
    const lastSell = campaign.sells[campaign.sells.length - 1]!;
    rows.push({
      mint: campaign.mint,
      route: route(campaign.mint),
      entryTs: campaign.firstBuy.timestamp,
      entryIso: new Date(campaign.firstBuy.timestamp * 1000).toISOString(),
      entrySolUsd,
      firstBuySol: sol(campaign.firstBuy.quoteLamports),
      entryTokens,
      entryMarketCapUsd,
      buys: campaign.buys.length,
      sells: campaign.sells.length,
      sourceExitRoiPct: (pnlLamports / costBasisSold) * 100,
      realizedPnlSol: sol(pnlLamports),
      holdMinutes: (lastSell.timestamp - campaign.firstBuy.timestamp) / 60,
    });
  }
  return rows.sort((a, b) => a.entryTs - b.entryTs);
}

function summarize(name: string, rows: Row[]): Bucket {
  const wins = rows.filter((row) => row.realizedPnlSol > 0);
  const profit = wins.reduce((sum, row) => sum + row.realizedPnlSol, 0);
  const loss = Math.abs(
    rows.filter((row) => row.realizedPnlSol < 0).reduce((sum, row) => sum + row.realizedPnlSol, 0)
  );
  const positiveRoi = rows
    .filter((row) => row.sourceExitRoiPct > 0)
    .reduce((sum, row) => sum + row.sourceExitRoiPct, 0);
  const negativeRoi = Math.abs(
    rows
      .filter((row) => row.sourceExitRoiPct < 0)
      .reduce((sum, row) => sum + row.sourceExitRoiPct, 0)
  );
  return {
    name,
    n: rows.length,
    winRatePct: rows.length ? (wins.length / rows.length) * 100 : 0,
    avgRoiPct: avg(rows.map((row) => row.sourceExitRoiPct)),
    medianRoiPct: median(rows.map((row) => row.sourceExitRoiPct)),
    profitFactor: negativeRoi > 0 ? positiveRoi / negativeRoi : positiveRoi > 0 ? null : 0,
    sourceCapitalProfitFactor: loss > 0 ? profit / loss : profit > 0 ? null : 0,
    totalPnlSol: rows.reduce((sum, row) => sum + row.realizedPnlSol, 0),
    medianEntryMcUsd: median(rows.map((row) => row.entryMarketCapUsd)),
    medianHoldMinutes: median(rows.map((row) => row.holdMinutes)),
  };
}

const BUCKETS: Array<{ name: string; test: (mc: number) => boolean }> = [
  { name: "$0-5k", test: (mc) => mc < 5_000 },
  { name: "$5k-10k", test: (mc) => mc >= 5_000 && mc < 10_000 },
  { name: "$10k-20k", test: (mc) => mc >= 10_000 && mc < 20_000 },
  { name: "$20k-50k", test: (mc) => mc >= 20_000 && mc < 50_000 },
  { name: "$50k-100k", test: (mc) => mc >= 50_000 && mc < 100_000 },
  { name: "$100k+", test: (mc) => mc >= 100_000 },
];

function bucketRows(rows: Row[]): Bucket[] {
  return BUCKETS.map((bucket) => summarize(bucket.name, rows.filter((row) => bucket.test(row.entryMarketCapUsd))));
}

function markdown(output: {
  generatedAt: string;
  wallet: string;
  coverage: Record<string, unknown>;
  solPriceSource: string;
  buckets: Record<string, Bucket[]>;
}): string {
  const sections = Object.entries(output.buckets).map(([scope, buckets]) => [
    `## ${scope === "all" ? "All Routes" : `${scope.toUpperCase()} Route`}`,
    "",
    "| Entry MC | n | Win | Avg ROI | Median ROI | PF | Total PnL | Median hold |",
    "|---|---:|---:|---:|---:|---:|---:|---:|",
    ...buckets.map((bucket) =>
      `| ${bucket.name} | ${bucket.n} | ${fmtPct(bucket.winRatePct)} | ${fmtPct(bucket.avgRoiPct)} | ${fmtPct(bucket.medianRoiPct)} | ${bucket.profitFactor?.toFixed(2) ?? "-"} | ${bucket.totalPnlSol >= 0 ? "+" : ""}${bucket.totalPnlSol.toFixed(2)} SOL | ${bucket.medianHoldMinutes?.toFixed(2) ?? "-"}m |`
    ),
  ].join("\n"));
  return [
    "# Wallet Ledger-First Entry Market-Cap Buckets",
    "",
    `Wallet: \`${output.wallet}\``,
    `Generated: ${output.generatedAt}`,
    "",
    "## Coverage",
    "",
    ...Object.entries(output.coverage).map(([key, value]) => `- ${key}: ${String(value)}`),
    `- SOL/USD source: ${output.solPriceSource}`,
    `- Entry MC: source first-fill price × ${SUPPLY.toLocaleString("en-US")} assumed supply`,
    "- Source ROI: campaign proceeds minus proportional sold cost basis; no candle or surviving-pool requirement",
    "",
    ...sections,
    "",
    "## Interpretation Notes",
    "",
    "- This report is the high-coverage source-exit bucket study. MFE/MAE and target-hit statistics remain in the smaller candle-enriched audit.",
    "- Route suffixes are used only for coarse segmentation (`pump`, `BAGS`, other).",
    "- Source-fill MC includes the source wallet's realized fill and therefore price impact; it is intentionally aligned with the live inferred-MC gate.",
    "",
  ].join("\n");
}

async function main(): Promise<void> {
  const wallet = process.argv[2] ?? "";
  const maxTxs = Number(process.argv[3] ?? "12001");
  const outputTag = process.argv[4] ?? `wallet-ledger-buckets-${wallet}-${maxTxs}`;
  if (!wallet) {
    throw new Error("usage: tsx src/walletLedgerBucketAudit.ts WALLET [maxTxs] [outputTag]");
  }
  const txs = await fetchWalletTxs(wallet, maxTxs);
  const fills = txs.map((tx) => extractFill(tx, wallet)).filter((fill): fill is Fill => fill != null);
  if (!fills.length) throw new Error("No SOL-quoted fills reconstructed");
  const campaigns = buildCampaigns(fills);
  const oldestTs = Math.min(...txs.map((tx) => tx.timestamp ?? Infinity));
  const newestTs = Math.max(...txs.map((tx) => tx.timestamp ?? 0));
  const solPrices = await loadSolPrices(oldestTs, newestTs);
  const rows = buildRows(campaigns, solPrices.prices);
  const output = {
    generatedAt: new Date().toISOString(),
    wallet,
    assumptions: { inferredTokenSupply: SUPPLY },
    coverage: {
      transactions: txs.length,
      reconstructedFills: fills.length,
      campaigns: campaigns.length,
      closedMeasuredCampaigns: rows.length,
      oldestTransaction: new Date(oldestTs * 1000).toISOString(),
      newestTransaction: new Date(newestTs * 1000).toISOString(),
      pumpRows: rows.filter((row) => row.route === "pump").length,
      bagsRows: rows.filter((row) => row.route === "bags").length,
      otherRows: rows.filter((row) => row.route === "other").length,
    },
    solPriceSource: solPrices.source,
    buckets: {
      all: bucketRows(rows),
      pump: bucketRows(rows.filter((row) => row.route === "pump")),
      bags: bucketRows(rows.filter((row) => row.route === "bags")),
      other: bucketRows(rows.filter((row) => row.route === "other")),
    },
    rows,
  };
  const dataDir = path.resolve(process.cwd(), "data");
  await mkdir(dataDir, { recursive: true });
  const jsonPath = path.join(dataDir, `${outputTag}.json`);
  const mdPath = path.join(dataDir, `${outputTag}.md`);
  await writeFile(jsonPath, JSON.stringify(output, null, 2), "utf8");
  await writeFile(mdPath, `${markdown(output)}\n`, "utf8");
  console.log(JSON.stringify({ jsonPath, mdPath, coverage: output.coverage }, null, 2));
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((err) => {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
