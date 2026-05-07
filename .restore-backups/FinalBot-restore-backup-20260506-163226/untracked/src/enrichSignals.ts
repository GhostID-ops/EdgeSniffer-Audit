import "dotenv/config";
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

interface SignalRow {
  raw: string[];
  mint: string;
  firstBoostMcUsd: number | null;
  unixSeconds: number;
}

interface DexEnrichment {
  pairFound: boolean;
  currentMcUsd: number | null;
  currentPriceUsd: number | null;
  liquidityUsd: number | null;
  volume24hUsd: number | null;
  priceChange1hPct: number | null;
  priceChange24hPct: number | null;
  pairCreatedMs: number | null;
  dex: string | null;
}

interface DexPair {
  chainId?: string;
  dexId?: string;
  baseToken?: { address?: string };
  priceUsd?: string;
  marketCap?: number;
  fdv?: number;
  liquidity?: { usd?: number };
  volume?: { h24?: number };
  priceChange?: { h1?: number; h24?: number };
  pairCreatedAt?: number;
}

interface DexResponse {
  pairs?: DexPair[] | null;
}

const BATCH_SIZE = 30;
const RATE_DELAY_MS = 250;

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function fetchDexBatch(addresses: string[]): Promise<DexPair[]> {
  if (addresses.length === 0) return [];
  const url = `https://api.dexscreener.com/latest/dex/tokens/${addresses.join(",")}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(15000) });
  if (!res.ok) {
    throw new Error(`DexScreener HTTP ${res.status} ${res.statusText}`);
  }
  const json = (await res.json()) as DexResponse;
  return json.pairs ?? [];
}

function pickBestPair(pairs: DexPair[], mint: string): DexPair | undefined {
  const candidates = pairs.filter(
    (p) =>
      p.chainId === "solana" &&
      p.baseToken?.address?.toLowerCase() === mint.toLowerCase()
  );
  if (candidates.length === 0) return undefined;
  candidates.sort((a, b) => (b.liquidity?.usd ?? 0) - (a.liquidity?.usd ?? 0));
  return candidates[0];
}

function pairToEnrichment(pair: DexPair | undefined): DexEnrichment {
  if (!pair) {
    return {
      pairFound: false,
      currentMcUsd: null,
      currentPriceUsd: null,
      liquidityUsd: null,
      volume24hUsd: null,
      priceChange1hPct: null,
      priceChange24hPct: null,
      pairCreatedMs: null,
      dex: null,
    };
  }
  const mc = pair.marketCap ?? pair.fdv ?? null;
  const priceUsd = pair.priceUsd ? parseFloat(pair.priceUsd) : null;
  return {
    pairFound: true,
    currentMcUsd: mc,
    currentPriceUsd: Number.isFinite(priceUsd) ? priceUsd : null,
    liquidityUsd: pair.liquidity?.usd ?? null,
    volume24hUsd: pair.volume?.h24 ?? null,
    priceChange1hPct: pair.priceChange?.h1 ?? null,
    priceChange24hPct: pair.priceChange?.h24 ?? null,
    pairCreatedMs: pair.pairCreatedAt ?? null,
    dex: pair.dexId ?? null,
  };
}

function readSignalsCsv(filePath: string): {
  header: string[];
  rows: SignalRow[];
} {
  const text = readFileSync(filePath, "utf8");
  const lines = text.split(/\r?\n/).filter((l) => l.length > 0);
  if (lines.length === 0) {
    return { header: [], rows: [] };
  }
  const header = lines[0].split(",");
  const mintIdx = header.indexOf("mint");
  const firstBoostIdx = header.indexOf("first_boost_mc_usd");
  const unixIdx = header.indexOf("unix_seconds");
  if (mintIdx < 0) throw new Error(`signals.csv missing 'mint' column`);

  const rows: SignalRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = lines[i].split(",");
    if (cells.length < header.length) continue;
    const fb = firstBoostIdx >= 0 ? parseFloat(cells[firstBoostIdx]) : NaN;
    const ux = unixIdx >= 0 ? parseInt(cells[unixIdx], 10) : 0;
    rows.push({
      raw: cells,
      mint: cells[mintIdx],
      firstBoostMcUsd: Number.isFinite(fb) ? fb : null,
      unixSeconds: ux,
    });
  }
  return { header, rows };
}

async function main(): Promise<void> {
  const inputPath =
    process.env.ENRICH_INPUT ?? path.resolve(process.cwd(), "signals.csv");
  const outputPath =
    process.env.ENRICH_OUTPUT ??
    path.resolve(process.cwd(), "signals_enriched.csv");

  console.log(`[enrich] input=${inputPath} output=${outputPath}`);

  const { header, rows } = readSignalsCsv(inputPath);
  console.log(`[enrich] read ${rows.length} signal rows`);

  const uniqueMints = Array.from(new Set(rows.map((r) => r.mint)));
  console.log(`[enrich] ${uniqueMints.length} unique mints to enrich`);

  const enrichment = new Map<string, DexEnrichment>();
  let processed = 0;
  let withPair = 0;

  for (let i = 0; i < uniqueMints.length; i += BATCH_SIZE) {
    const batch = uniqueMints.slice(i, i + BATCH_SIZE);
    try {
      const pairs = await fetchDexBatch(batch);
      for (const mint of batch) {
        const pair = pickBestPair(pairs, mint);
        const data = pairToEnrichment(pair);
        enrichment.set(mint, data);
        if (data.pairFound) withPair++;
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[enrich] batch failed (${msg}) — marking ${batch.length} as no-data`);
      for (const mint of batch) {
        enrichment.set(mint, pairToEnrichment(undefined));
      }
    }
    processed += batch.length;
    if (processed % 120 === 0 || processed === uniqueMints.length) {
      console.log(
        `[enrich] ${processed}/${uniqueMints.length} mints processed (${withPair} alive on DexScreener)`
      );
    }
    await sleep(RATE_DELAY_MS);
  }

  // Write enriched CSV: original columns + enrichment columns + computed gain.
  const newCols = [
    "pair_found",
    "current_mc_usd",
    "current_price_usd",
    "liquidity_usd",
    "volume_24h_usd",
    "price_change_1h_pct",
    "price_change_24h_pct",
    "pair_created_ms",
    "dex",
    "gain_vs_first_boost_pct",
    "days_since_signal",
  ];

  const outLines: string[] = [];
  outLines.push([...header, ...newCols].join(","));

  const nowMs = Date.now();
  for (const row of rows) {
    const e = enrichment.get(row.mint) ?? pairToEnrichment(undefined);
    const gainPct =
      e.currentMcUsd != null && row.firstBoostMcUsd && row.firstBoostMcUsd > 0
        ? ((e.currentMcUsd - row.firstBoostMcUsd) / row.firstBoostMcUsd) * 100
        : null;
    const daysSince =
      row.unixSeconds > 0
        ? Math.round((nowMs / 1000 - row.unixSeconds) / 8640) / 10
        : null;
    const extra = [
      e.pairFound ? "true" : "false",
      e.currentMcUsd ?? "",
      e.currentPriceUsd ?? "",
      e.liquidityUsd ?? "",
      e.volume24hUsd ?? "",
      e.priceChange1hPct ?? "",
      e.priceChange24hPct ?? "",
      e.pairCreatedMs ?? "",
      e.dex ?? "",
      gainPct != null ? gainPct.toFixed(2) : "",
      daysSince ?? "",
    ];
    outLines.push([...row.raw, ...extra].join(","));
  }

  writeFileSync(outputPath, outLines.join("\n") + "\n", "utf8");
  console.log(`[enrich] wrote ${rows.length} rows → ${outputPath}`);
  console.log(
    `[enrich] coverage: ${withPair}/${uniqueMints.length} (${(
      (withPair / Math.max(1, uniqueMints.length)) *
      100
    ).toFixed(1)}%) of mints had DexScreener data`
  );
  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
