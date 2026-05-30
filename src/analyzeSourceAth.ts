import "dotenv/config";
import { PublicKey, type VersionedTransactionResponse } from "@solana/web3.js";
import { heliusAddressTransactionsUrl, readConnection } from "./config.js";
import { getTokenPairInfo } from "./tokenRegistry.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const GECKO_BASE = "https://api.geckoterminal.com/api/v2";
const MORALIS_BASE = "https://solana-gateway.moralis.io";
const MORALIS_API_KEY = process.env.MORALIS_API_KEY ?? "";
const ATH_PRICE_PROVIDER = (process.env.ATH_PRICE_PROVIDER ?? "GECKO").toUpperCase();

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

type Ohlcv = {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
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

function fmtSol(lamports: number): string {
  return `${sol(lamports).toFixed(4)} SOL`;
}

function priceSolPerTokenRaw(solLamports: number, tokenRaw: number): number | null {
  if (solLamports <= 0 || tokenRaw <= 0) return null;
  return solLamports / tokenRaw;
}

function extractSwap(tx: HeliusTx, wallet: string): Swap | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, number>();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      if (!mint || mint === WRAPPED_SOL_MINT) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
    }
  }

  let mint: string | null = null;
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
    };
  }
  return null;
}

async function fetchWalletTxs(wallet: string, limit: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < limit) {
    const batchLimit = Math.min(100, limit - all.length);
    const url = heliusAddressTransactionsUrl(wallet, before, batchLimit);
    if (!url) throw new Error("HELIUS_API_KEY is not set");
    let response = await fetch(url, { signal: AbortSignal.timeout(12_000) });
    if (response.status === 429) {
      await sleep(65_000);
      response = await fetch(url, { signal: AbortSignal.timeout(12_000) });
    }
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`Helius ${response.status}: ${text.slice(0, 180)}`);
    }
    const batch = (await response.json()) as HeliusTx[];
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  return all;
}

function resolveAccountKeys(tx: VersionedTransactionResponse): string[] {
  const message = tx.transaction.message;
  const staticKeys = (
    "staticAccountKeys" in message
      ? message.staticAccountKeys
      : (message as unknown as { accountKeys: PublicKey[] }).accountKeys ?? []
  ) as PublicKey[];
  const loaded = tx.meta?.loadedAddresses;
  const writable = loaded?.writable ?? [];
  const readonly = loaded?.readonly ?? [];
  return [...staticKeys, ...writable, ...readonly].map((k) =>
    typeof k === "string" ? k : k.toBase58()
  );
}

function extractSwapFromRpcTx(tx: VersionedTransactionResponse, wallet: string, signature: string): Swap | null {
  const meta = tx.meta;
  if (!meta || meta.err) return null;
  const accountKeys = resolveAccountKeys(tx);
  const walletIdx = accountKeys.indexOf(wallet);
  if (walletIdx < 0) return null;

  const fee = meta.fee ?? 0;
  const isSigner = walletIdx === 0;
  const nativeNet =
    (meta.postBalances?.[walletIdx] ?? 0) - (meta.preBalances?.[walletIdx] ?? 0) + (isSigner ? fee : 0);

  type TokenSnap = { mint: string; amount: bigint };
  const pre = new Map<number, TokenSnap>();
  for (const bal of meta.preTokenBalances ?? []) {
    if (bal.owner !== wallet) continue;
    pre.set(bal.accountIndex, {
      mint: bal.mint,
      amount: BigInt(bal.uiTokenAmount.amount),
    });
  }

  const tokenNet = new Map<string, bigint>();
  const handled = new Set<number>();
  for (const bal of meta.postTokenBalances ?? []) {
    if (bal.owner !== wallet) continue;
    handled.add(bal.accountIndex);
    const before = pre.get(bal.accountIndex)?.amount ?? 0n;
    const after = BigInt(bal.uiTokenAmount.amount);
    const delta = after - before;
    if (delta !== 0n) tokenNet.set(bal.mint, (tokenNet.get(bal.mint) ?? 0n) + delta);
  }
  for (const [accountIndex, before] of pre) {
    if (handled.has(accountIndex)) continue;
    tokenNet.set(before.mint, (tokenNet.get(before.mint) ?? 0n) - before.amount);
  }

  let mint: string | null = null;
  let raw = 0n;
  for (const [candidate, delta] of tokenNet) {
    if (candidate === WRAPPED_SOL_MINT) continue;
    if (delta < 0n ? -delta > (raw < 0n ? -raw : raw) : delta > (raw < 0n ? -raw : raw)) {
      mint = candidate;
      raw = delta;
    }
  }
  if (!mint || raw === 0n || nativeNet === 0) return null;
  const tokenRaw = Number(raw < 0n ? -raw : raw);
  if (!Number.isFinite(tokenRaw) || tokenRaw <= 0) return null;

  if (nativeNet < 0 && raw > 0n) {
    return {
      signature,
      timestamp: tx.blockTime ?? 0,
      side: "buy",
      mint,
      solLamports: Math.abs(nativeNet),
      tokenRaw,
    };
  }
  if (nativeNet > 0 && raw < 0n) {
    return {
      signature,
      timestamp: tx.blockTime ?? 0,
      side: "sell",
      mint,
      solLamports: nativeNet,
      tokenRaw,
    };
  }
  return null;
}

async function fetchRpcSwaps(wallet: string, limit: number): Promise<Swap[]> {
  const owner = new PublicKey(wallet);
  const signatures = await readConnection.getSignaturesForAddress(owner, { limit }, "confirmed");
  const swaps: Swap[] = [];
  for (const sig of signatures) {
    const tx = await readConnection.getTransaction(sig.signature, {
      maxSupportedTransactionVersion: 0,
      commitment: "confirmed",
    });
    if (!tx) continue;
    const swap = extractSwapFromRpcTx(tx, wallet, sig.signature);
    if (swap) swaps.push(swap);
    await sleep(120);
  }
  return swaps.sort((a, b) => a.timestamp - b.timestamp);
}

async function fetchSourceSwaps(wallet: string, limit: number): Promise<Swap[]> {
  const historyUrl = heliusAddressTransactionsUrl(wallet, undefined, 1);
  if (!historyUrl) {
    console.log("HELIUS_API_KEY not set; using RPC getSignaturesForAddress/getTransaction fallback.");
    return fetchRpcSwaps(wallet, limit);
  }
  const txs = await fetchWalletTxs(wallet, limit);
  return txs
    .map((tx) => extractSwap(tx, wallet))
    .filter((swap): swap is Swap => swap != null)
    .sort((a, b) => a.timestamp - b.timestamp);
}

function buildCampaigns(swaps: Swap[]): Campaign[] {
  const byMint = new Map<string, Campaign>();
  for (const swap of swaps) {
    let campaign = byMint.get(swap.mint);
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

async function fetchGeckoOhlcv(
  poolAddress: string,
  entryTs: number,
  untilTs: number
): Promise<Ohlcv[]> {
  const ageSec = Math.max(0, untilTs - entryTs);
  const timeframe = ageSec <= 3 * 24 * 60 * 60 ? "minute" : "hour";
  const aggregate = 1;
  const rows: Ohlcv[] = [];
  let before = untilTs + 60;

  for (let page = 0; page < 8; page++) {
    const url =
      `${GECKO_BASE}/networks/solana/pools/${poolAddress}/ohlcv/${timeframe}` +
      `?aggregate=${aggregate}&before_timestamp=${before}&limit=1000&currency=usd`;
    const response = await fetch(url, { signal: AbortSignal.timeout(12_000) });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(`GeckoTerminal ${response.status}: ${text.slice(0, 160)}`);
    }
    const json = (await response.json()) as {
      data?: { attributes?: { ohlcv_list?: number[][] } };
    };
    const batch = json.data?.attributes?.ohlcv_list ?? [];
    if (batch.length === 0) break;
    for (const row of batch) {
      const [ts, open, high, low, close, volume] = row;
      if ([ts, open, high, low, close].some((v) => typeof v !== "number")) continue;
      if (ts >= entryTs && ts <= untilTs) rows.push({ ts, open, high, low, close, volume: volume ?? 0 });
    }
    const oldest = Math.min(...batch.map((row) => row[0] ?? before));
    if (!Number.isFinite(oldest) || oldest <= entryTs) break;
    before = oldest - 1;
    await sleep(2_500);
  }

  return rows.sort((a, b) => a.ts - b.ts);
}

async function fetchMoralisOhlcv(
  poolAddress: string,
  entryTs: number,
  untilTs: number
): Promise<Ohlcv[]> {
  if (!MORALIS_API_KEY) throw new Error("MORALIS_API_KEY is not set");

  const ageSec = Math.max(0, untilTs - entryTs);
  const timeframe = ageSec <= 6 * 60 * 60 ? "1min" : ageSec <= 3 * 24 * 60 * 60 ? "5min" : "1h";
  const rows: Ohlcv[] = [];
  let cursor: string | undefined;

  for (let page = 0; page < 20; page++) {
    const params = new URLSearchParams({
      fromDate: String(entryTs),
      toDate: String(untilTs),
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
      result?: Array<{
        timestamp?: string;
        open?: number;
        high?: number;
        low?: number;
        close?: number;
        volume?: number;
      }>;
    };
    for (const item of json.result ?? []) {
      const ts = item.timestamp ? Math.floor(new Date(item.timestamp).getTime() / 1000) : NaN;
      const { open, high, low, close } = item;
      if (!Number.isFinite(ts) || typeof open !== "number" || typeof high !== "number" || typeof low !== "number" || typeof close !== "number") {
        continue;
      }
      if (ts >= entryTs && ts <= untilTs) {
        rows.push({ ts, open, high, low, close, volume: item.volume ?? 0 });
      }
    }
    cursor = json.cursor ?? undefined;
    if (!cursor) break;
    await sleep(250);
  }

  return rows.sort((a, b) => a.ts - b.ts);
}

async function fetchOhlcv(poolAddress: string, entryTs: number, untilTs: number): Promise<{
  provider: string;
  rows: Ohlcv[];
}> {
  if (ATH_PRICE_PROVIDER === "MORALIS") {
    try {
      return { provider: "moralis", rows: await fetchMoralisOhlcv(poolAddress, entryTs, untilTs) };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Moralis OHLCV failed for ${poolAddress.slice(0, 8)}...: ${msg}; falling back to GeckoTerminal.`);
    }
  }
  return { provider: "gecko", rows: await fetchGeckoOhlcv(poolAddress, entryTs, untilTs) };
}

async function analyzeCampaign(campaign: Campaign, nowTs: number): Promise<{
  campaign: Campaign;
  symbol: string;
  pool: string | null;
  entryUsd: number | null;
  athUsd: number | null;
  athTs: number | null;
  athPct: number | null;
  hit30: boolean;
  sourceExitPct: number | null;
  notes: string;
}> {
  const pair = await getTokenPairInfo(campaign.mint);
  if (!pair?.pairAddress) {
    return {
      campaign,
      symbol: short(campaign.mint),
      pool: null,
      entryUsd: null,
      athUsd: null,
      athTs: null,
      athPct: null,
      hit30: false,
      sourceExitPct: null,
      notes: "no pool found",
    };
  }

  const fetched = await fetchOhlcv(pair.pairAddress, campaign.firstBuy.timestamp, nowTs);
  const rows = fetched.rows;
  const afterEntry = rows.filter((row) => row.ts >= campaign.firstBuy.timestamp);
  const entry = afterEntry[0]?.open ?? afterEntry[0]?.close ?? null;
  const peak = afterEntry.reduce<Ohlcv | null>((best, row) => (!best || row.high > best.high ? row : best), null);
  const athPct = entry && peak ? ((peak.high - entry) / entry) * 100 : null;

  const avgEntrySol = priceSolPerTokenRaw(campaign.spentLamports, campaign.boughtRaw);
  const avgExitSol = priceSolPerTokenRaw(campaign.receivedLamports, Math.min(campaign.soldRaw, campaign.boughtRaw));
  const sourceExitPct =
    avgEntrySol != null && avgExitSol != null ? ((avgExitSol - avgEntrySol) / avgEntrySol) * 100 : null;

  return {
    campaign,
    symbol: pair.baseToken.symbol || short(campaign.mint),
    pool: pair.pairAddress,
    entryUsd: entry,
    athUsd: peak?.high ?? null,
    athTs: peak?.ts ?? null,
    athPct,
    hit30: athPct != null && athPct >= 30,
    sourceExitPct,
    notes: rows.length === 0 ? `${fetched.provider}: no ohlcv rows after entry` : `${fetched.provider}: ${rows.length} candles`,
  };
}

function median(values: number[]): number | null {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  return sorted[Math.floor(sorted.length / 2)] ?? null;
}

async function main(): Promise<void> {
  const wallet = (process.argv[2] ?? process.env.TRACKED_WALLETS?.split(",")[0] ?? "").trim();
  const walletTxLimit = Number(process.argv[3] ?? "160");
  const mintLimit = Number(process.argv[4] ?? "25");
  if (!wallet) throw new Error("Pass wallet arg or set TRACKED_WALLETS");

  console.log(`Analyzing source ATH for ${short(wallet)} txLimit=${walletTxLimit} mintLimit=${mintLimit}`);
  const swaps = await fetchSourceSwaps(wallet, walletTxLimit);
  const campaigns = buildCampaigns(swaps).slice(0, mintLimit);
  const nowTs = Math.floor(Date.now() / 1000);
  const rows = [];

  for (const campaign of campaigns) {
    try {
      rows.push(await analyzeCampaign(campaign, nowTs));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      rows.push({
        campaign,
        symbol: short(campaign.mint),
        pool: null,
        entryUsd: null,
        athUsd: null,
        athTs: null,
        athPct: null,
        hit30: false,
        sourceExitPct: null,
        notes: msg,
      });
    }
    await sleep(2_500);
  }

  const measured = rows.filter((row) => row.athPct != null);
  const hit30 = measured.filter((row) => row.hit30);
  const athPcts = measured.map((row) => row.athPct!).filter(Number.isFinite);
  const sourceExitPcts = measured.map((row) => row.sourceExitPct).filter((v): v is number => v != null);

  console.log("");
  console.log("Summary");
  console.log(`  Campaigns: ${rows.length}; measured ATH: ${measured.length}`);
  console.log(`  Hit +30% after source entry: ${hit30.length}/${measured.length} (${measured.length ? ((hit30.length / measured.length) * 100).toFixed(1) : "0.0"}%)`);
  console.log(`  Avg post-entry ATH: ${fmtPct(athPcts.reduce((a, b) => a + b, 0) / Math.max(1, athPcts.length))}`);
  console.log(`  Median post-entry ATH: ${fmtPct(median(athPcts))}`);
  console.log(`  Avg source realized exit: ${fmtPct(sourceExitPcts.reduce((a, b) => a + b, 0) / Math.max(1, sourceExitPcts.length))}`);
  console.log("");
  console.log("Rows");
  for (const row of rows) {
    const buyDate = new Date(row.campaign.firstBuy.timestamp * 1000).toISOString();
    const athDate = row.athTs ? new Date(row.athTs * 1000).toISOString() : "-";
    console.log(
      [
        short(row.campaign.mint),
        row.symbol,
        `buy=${fmtSol(row.campaign.spentLamports)} (${row.campaign.buys.length}x)`,
        `sourceExit=${fmtPct(row.sourceExitPct)}`,
        `postEntryATH=${fmtPct(row.athPct)}`,
        `hit30=${row.hit30 ? "yes" : "no"}`,
        `buyAt=${buyDate}`,
        `athAt=${athDate}`,
        row.notes,
      ].join(" | ")
    );
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
