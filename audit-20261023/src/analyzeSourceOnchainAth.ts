import "dotenv/config";
import { heliusAddressTransactionsUrl } from "./config.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

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
  spentLamports: number;
  boughtRaw: number;
  receivedLamports: number;
  soldRaw: number;
  buys: number;
  sells: number;
};

type PricePoint = {
  signature: string;
  timestamp: number;
  wallet: string;
  side: "buy" | "sell";
  priceLamportsPerRaw: number;
  solLamports: number;
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

async function fetchAddressTxs(address: string, limit: number, before?: string): Promise<HeliusTx[]> {
  const url = heliusAddressTransactionsUrl(address, before, limit);
  if (!url) throw new Error("HELIUS_API_KEY is not set");
  const response = await fetch(url, { signal: AbortSignal.timeout(12_000) });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Helius ${response.status}: ${text.slice(0, 220)}`);
  }
  return (await response.json()) as HeliusTx[];
}

async function fetchPagedAddressTxs(address: string, limit: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < limit) {
    const batch = await fetchAddressTxs(address, Math.min(100, limit - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  return all;
}

async function fetchMintTxsSince(mint: string, entryTs: number, maxTxs: number): Promise<HeliusTx[]> {
  const all: HeliusTx[] = [];
  let before: string | undefined;
  while (all.length < maxTxs) {
    const batch = await fetchAddressTxs(mint, Math.min(100, maxTxs - all.length), before);
    if (batch.length === 0) break;
    all.push(...batch);
    const oldest = batch[batch.length - 1]?.timestamp ?? 0;
    if (oldest > 0 && oldest < entryTs) break;
    before = batch[batch.length - 1]?.signature;
    await sleep(650);
  }
  return all.filter((tx) => (tx.timestamp ?? 0) >= entryTs);
}

function extractSourceSwap(tx: HeliusTx, wallet: string): Swap | null {
  let nativeNet = 0;
  const tokenNet = new Map<string, number>();
  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      if (!mint) continue;
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
  const quoteNet = tokenNet.get(WRAPPED_SOL_MINT) ?? 0;
  const quoteLamports =
    tokenRaw > 0 && quoteNet < 0
      ? Math.abs(quoteNet)
      : tokenRaw < 0 && quoteNet > 0
      ? quoteNet
      : tokenRaw > 0 && nativeNet < 0
      ? Math.abs(nativeNet)
      : tokenRaw < 0 && nativeNet > 0
      ? nativeNet
      : 0;

  if (!mint || tokenRaw === 0 || quoteLamports <= 0) return null;
  if (tokenRaw > 0) {
    return { signature: tx.signature, timestamp: tx.timestamp ?? 0, side: "buy", mint, solLamports: quoteLamports, tokenRaw: Math.abs(tokenRaw) };
  }
  if (tokenRaw < 0) {
    return { signature: tx.signature, timestamp: tx.timestamp ?? 0, side: "sell", mint, solLamports: quoteLamports, tokenRaw: Math.abs(tokenRaw) };
  }
  return null;
}

function extractMintPricePoints(tx: HeliusTx, mint: string): PricePoint[] {
  const nativeByWallet = new Map<string, number>();
  const tokenByWallet = new Map<string, number>();
  const wsolByWallet = new Map<string, number>();
  for (const account of tx.accountData ?? []) {
    if (account.nativeBalanceChange) {
      nativeByWallet.set(account.account, (nativeByWallet.get(account.account) ?? 0) + account.nativeBalanceChange);
    }
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.mint !== mint || !change.userAccount) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      tokenByWallet.set(change.userAccount, (tokenByWallet.get(change.userAccount) ?? 0) + raw);
    }
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.mint !== WRAPPED_SOL_MINT || !change.userAccount) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      wsolByWallet.set(change.userAccount, (wsolByWallet.get(change.userAccount) ?? 0) + raw);
    }
  }

  const points: PricePoint[] = [];
  for (const [wallet, tokenNet] of tokenByWallet) {
    const nativeNet = nativeByWallet.get(wallet) ?? 0;
    const wsolNet = wsolByWallet.get(wallet) ?? 0;
    if ((nativeNet === 0 && wsolNet === 0) || tokenNet === 0) continue;
    const tokenAbs = Math.abs(tokenNet);
    if (tokenAbs <= 0) continue;
    const quoteLamports =
      tokenNet > 0 && wsolNet < 0
        ? Math.abs(wsolNet)
        : tokenNet < 0 && wsolNet > 0
        ? wsolNet
        : tokenNet > 0 && nativeNet < 0
        ? Math.abs(nativeNet)
        : tokenNet < 0 && nativeNet > 0
        ? nativeNet
        : 0;
    if (quoteLamports <= 0) continue;
    if (tokenNet > 0) {
      points.push({
        signature: tx.signature,
        timestamp: tx.timestamp ?? 0,
        wallet,
        side: "buy",
        priceLamportsPerRaw: quoteLamports / tokenAbs,
        solLamports: quoteLamports,
      });
    } else if (tokenNet < 0) {
      points.push({
        signature: tx.signature,
        timestamp: tx.timestamp ?? 0,
        wallet,
        side: "sell",
        priceLamportsPerRaw: quoteLamports / tokenAbs,
        solLamports: quoteLamports,
      });
    }
  }
  return points;
}

function buildCampaigns(swaps: Swap[]): Campaign[] {
  const campaigns = new Map<string, Campaign>();
  for (const swap of swaps) {
    let campaign = campaigns.get(swap.mint);
    if (!campaign) {
      if (swap.side !== "buy") continue;
      campaign = {
        mint: swap.mint,
        firstBuy: swap,
        spentLamports: 0,
        boughtRaw: 0,
        receivedLamports: 0,
        soldRaw: 0,
        buys: 0,
        sells: 0,
      };
      campaigns.set(swap.mint, campaign);
    }
    if (swap.side === "buy") {
      campaign.spentLamports += swap.solLamports;
      campaign.boughtRaw += swap.tokenRaw;
      campaign.buys++;
    } else {
      campaign.firstSell ??= swap;
      campaign.receivedLamports += swap.solLamports;
      campaign.soldRaw += swap.tokenRaw;
      campaign.sells++;
    }
  }
  return [...campaigns.values()].sort((a, b) => b.firstBuy.timestamp - a.firstBuy.timestamp);
}

function median(values: number[]): number | null {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  return sorted[Math.floor(sorted.length / 2)] ?? null;
}

async function main(): Promise<void> {
  const wallet = (process.argv[2] ?? process.env.TRACKED_WALLETS?.split(",")[0] ?? "").trim();
  const walletTxLimit = Number(process.argv[3] ?? "300");
  const mintLimit = Number(process.argv[4] ?? "30");
  const mintTxLimit = Number(process.argv[5] ?? "700");
  const minPointSol = Number(process.argv[6] ?? "0.002");
  const minPointLamports = Math.round(minPointSol * 1_000_000_000);
  if (!wallet) throw new Error("Pass wallet arg or set TRACKED_WALLETS");

  console.log(
    `Helius on-chain ATH for ${short(wallet)} txLimit=${walletTxLimit} mintLimit=${mintLimit} ` +
      `mintTxLimit=${mintTxLimit} minPoint=${minPointSol} SOL`
  );
  const walletTxs = await fetchPagedAddressTxs(wallet, walletTxLimit);
  const swaps = walletTxs
    .map((tx) => extractSourceSwap(tx, wallet))
    .filter((swap): swap is Swap => swap != null)
    .sort((a, b) => a.timestamp - b.timestamp);
  const campaigns = buildCampaigns(swaps).slice(0, mintLimit);

  const rows = [];
  for (const campaign of campaigns) {
    try {
      const entryPrice = campaign.spentLamports / campaign.boughtRaw;
      const firstBuyEntryPrice = campaign.firstBuy.solLamports / campaign.firstBuy.tokenRaw;
      const txs = await fetchMintTxsSince(campaign.mint, campaign.firstBuy.timestamp, mintTxLimit);
      const points = txs
        .flatMap((tx) => extractMintPricePoints(tx, campaign.mint))
        .filter((point) => point.solLamports >= minPointLamports);
      const buys = points.filter((point) => point.side === "buy");
      const peak = buys.reduce<PricePoint | null>(
        (best, point) => (!best || point.priceLamportsPerRaw > best.priceLamportsPerRaw ? point : best),
        null
      );
      const trough = points.reduce<PricePoint | null>(
        (best, point) => (!best || point.priceLamportsPerRaw < best.priceLamportsPerRaw ? point : best),
        null
      );
      const hit30Point = buys
        .sort((a, b) => a.timestamp - b.timestamp)
        .find((point) => ((point.priceLamportsPerRaw - firstBuyEntryPrice) / firstBuyEntryPrice) * 100 >= 30);
      const before30 = hit30Point
        ? points.filter((point) => point.timestamp <= hit30Point.timestamp)
        : points;
      const troughBefore30 = before30.reduce<PricePoint | null>(
        (best, point) => (!best || point.priceLamportsPerRaw < best.priceLamportsPerRaw ? point : best),
        null
      );
      const athPct = peak ? ((peak.priceLamportsPerRaw - firstBuyEntryPrice) / firstBuyEntryPrice) * 100 : null;
      const maePct = trough ? ((trough.priceLamportsPerRaw - firstBuyEntryPrice) / firstBuyEntryPrice) * 100 : null;
      const drawdownBefore30Pct = troughBefore30
        ? ((troughBefore30.priceLamportsPerRaw - firstBuyEntryPrice) / firstBuyEntryPrice) * 100
        : null;
      const timeTo30Sec = hit30Point ? hit30Point.timestamp - campaign.firstBuy.timestamp : null;
      const exitPrice = campaign.soldRaw > 0 ? campaign.receivedLamports / Math.min(campaign.soldRaw, campaign.boughtRaw) : null;
      const sourceExitPct = exitPrice != null ? ((exitPrice - firstBuyEntryPrice) / firstBuyEntryPrice) * 100 : null;
      rows.push({
        campaign,
        athPct,
        maePct,
        drawdownBefore30Pct,
        timeTo30Sec,
        peak,
        trough,
        sourceExitPct,
        points: points.length,
        error: null as string | null,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      rows.push({
        campaign,
        athPct: null,
        maePct: null,
        drawdownBefore30Pct: null,
        timeTo30Sec: null,
        peak: null,
        trough: null,
        sourceExitPct: null,
        points: 0,
        error: msg,
      });
    }
    await sleep(700);
  }

  const measured = rows.filter((row) => row.athPct != null);
  const pcts = measured.map((row) => row.athPct!).filter(Number.isFinite);
  const hit30 = measured.filter((row) => (row.athPct ?? 0) >= 30);
  const hit50 = measured.filter((row) => (row.athPct ?? 0) >= 50);
  const hit100 = measured.filter((row) => (row.athPct ?? 0) >= 100);
  const hit30Times = measured.map((row) => row.timeTo30Sec).filter((value): value is number => value != null);
  const maes = measured.map((row) => row.maePct).filter((value): value is number => value != null);
  const ddTo30 = measured.map((row) => row.drawdownBefore30Pct).filter((value): value is number => value != null);

  console.log("");
  console.log("Summary");
  console.log(`  Campaigns: ${rows.length}; measured: ${measured.length}`);
  console.log(`  Hit +30%: ${hit30.length}/${measured.length} (${measured.length ? ((hit30.length / measured.length) * 100).toFixed(1) : "0.0"}%)`);
  console.log(`  Hit +50%: ${hit50.length}/${measured.length} (${measured.length ? ((hit50.length / measured.length) * 100).toFixed(1) : "0.0"}%)`);
  console.log(`  Hit +100%: ${hit100.length}/${measured.length} (${measured.length ? ((hit100.length / measured.length) * 100).toFixed(1) : "0.0"}%)`);
  console.log(`  Avg observed post-entry high: ${fmtPct(pcts.reduce((a, b) => a + b, 0) / Math.max(1, pcts.length))}`);
  console.log(`  Median observed post-entry high: ${fmtPct(median(pcts))}`);
  console.log(`  Median observed post-entry low: ${fmtPct(median(maes))}`);
  console.log(`  Median drawdown before +30: ${fmtPct(median(ddTo30))}`);
  console.log(`  Median time to +30: ${hit30Times.length ? `${Math.round(median(hit30Times)!)}s` : "-"}`);
  console.log("");
  console.log("Rows");
  for (const row of rows) {
    const buyAt = new Date(row.campaign.firstBuy.timestamp * 1000).toISOString();
    const peakAt = row.peak ? new Date(row.peak.timestamp * 1000).toISOString() : "-";
    console.log(
      [
        short(row.campaign.mint),
        `buy=${fmtSol(row.campaign.spentLamports)} (${row.campaign.buys}x)`,
        `sourceExit=${fmtPct(row.sourceExitPct)}`,
        `observedHigh=${fmtPct(row.athPct)}`,
        `observedLow=${fmtPct(row.maePct)}`,
        `hit30=${(row.athPct ?? -Infinity) >= 30 ? "yes" : "no"}`,
        `t30=${row.timeTo30Sec == null ? "-" : `${row.timeTo30Sec}s`}`,
        `ddTo30=${fmtPct(row.drawdownBefore30Pct)}`,
        `points=${row.points}`,
        `buyAt=${buyAt}`,
        `peakAt=${peakAt}`,
        row.error ? `error=${row.error}` : "",
      ].filter(Boolean).join(" | ")
    );
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
