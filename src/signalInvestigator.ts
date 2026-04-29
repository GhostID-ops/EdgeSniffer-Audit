import "dotenv/config";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { PublicKey } from "@solana/web3.js";
import { heliusEnhancedTransactionsUrl, readConnection } from "./config.js";

type SourceSummaryFile = {
  summary: {
    wallet: string;
    txFetched: number;
    swapRows: number;
    uniqueMints: number;
    buys: number;
    sells: number;
    unknown: number;
    first: string;
    last: string;
    buySize?: {
      p25?: number;
      median?: number;
      p75?: number;
      topBuckets?: Array<[string, number]>;
    };
    closedMints: number;
    wins: number;
    losses: number;
    winRate: number;
    totalSpent: number;
    totalReceived: number;
    net: number;
    holdFirstMedian?: number;
    holdLastMedian?: number;
  };
  groups: CampaignGroup[];
  rows: SourceRow[];
};

type CampaignGroup = {
  mint: string;
  first: string;
  last: string;
  buys: number;
  sells: number;
  unknown: number;
  spent: number;
  received: number;
  grossPnL: number;
  fees: number;
  net: number;
  holdFirstSec?: number;
  holdLastSec?: number;
  openToken?: number;
  sources?: string;
};

type SourceRow = {
  time: string;
  ts: number;
  sig: string;
  source?: string;
  type?: string;
  side: string;
  mint?: string;
  tokenNet?: number;
  nativeSol?: number;
  feeSol?: number;
  tipSol?: number;
  err?: string;
};

type CampaignModel = {
  group: CampaignGroup;
  rows: SourceRow[];
  buyRows: SourceRow[];
  sellRows: SourceRow[];
  firstBuy?: SourceRow;
  firstBuySize: number;
  maxBuySize: number;
  buySizes: number[];
  buyIntervalsSec: number[];
  sellIntervalsFromFirstBuySec: number[];
  durationSec: number;
  isWinner: boolean;
  isOpen: boolean;
  roi: number;
  sizeProfile: string;
  ladderProfile: string;
};

type ParsedTx = {
  signature: string;
  timestamp?: number;
  type?: string;
  source?: string;
  feePayer?: string;
  tokenTransfers?: Array<{
    mint?: string;
    fromUserAccount?: string;
    toUserAccount?: string;
    tokenAmount?: number;
  }>;
  accountData?: Array<{
    account?: string;
    nativeBalanceChange?: number;
    tokenBalanceChanges?: Array<{
      userAccount?: string;
      tokenAccount?: string;
      mint?: string;
      rawTokenAmount?: {
        tokenAmount?: string;
        decimals?: number;
      };
    }>;
  }>;
};

type MintChainCache = {
  mint: string;
  fetchedAt: string;
  searchedAddresses?: string[];
  signatures: Array<{ signature: string; blockTime?: number | null; err?: unknown }>;
  parsed: ParsedTx[];
};

type DeepMintSignal = {
  mint: string;
  net: number;
  isWinner: boolean;
  firstBuyTs: number;
  earliestSeenTs?: number;
  sourceEntryAgeSec?: number;
  sourceBuyerRank?: number;
  preSourceTxCount: number;
  preSourceActors: string[];
  preSourceActorLeadSec: Record<string, number>;
  closestPreSourceActors: string[];
  possibleCreator?: string;
  sourceFirstBuySignature?: string;
  historyQuality: "good" | "partial" | "poor";
};

type LeadActorStats = {
  actor: string;
  campaigns: number;
  wins: number;
  losses: number;
  net: number;
  avgLeadSec: number;
  examples: string[];
};

type DeepRunResult = {
  mintSignals: DeepMintSignal[];
  leadActors: LeadActorStats[];
  creators: Array<Record<string, string | number>>;
  requestedMints: number;
  completedMints: number;
  stoppedEarly: boolean;
  stopReason?: string;
};

const ROOT = process.cwd();
const SUMMARY_PATH = path.join(ROOT, "source-wallet-analysis-summary.json");
const REPORT_PATH = path.join(ROOT, "signal-investigation-report.md");
const OUTPUT_PATH = path.join(ROOT, "signal-investigation-output.json");
const CACHE_DIR = path.join(ROOT, ".cache", "signal-investigation");
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

class HeliusQuotaError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "HeliusQuotaError";
  }
}

function hasFlag(name: string): boolean {
  return process.argv.includes(name);
}

function numberArg(name: string, fallback: number): number {
  const prefix = `${name}=`;
  const raw = process.argv.find((arg) => arg.startsWith(prefix))?.slice(prefix.length);
  if (!raw) return fallback;
  const n = Number(raw);
  return Number.isFinite(n) ? n : fallback;
}

async function loadJson<T>(filePath: string): Promise<T> {
  return JSON.parse(await readFile(filePath, "utf8")) as T;
}

function sortByTs<T extends { ts?: number }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0));
}

function rowBuySize(row: SourceRow): number {
  if (row.side !== "buy") return 0;
  const native = Number(row.nativeSol ?? 0);
  return native < 0 ? Math.abs(native) : 0;
}

function rowSellReceive(row: SourceRow): number {
  if (row.side !== "sell") return 0;
  const native = Number(row.nativeSol ?? 0);
  return native > 0 ? native : 0;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[mid - 1]! + sorted[mid]!) / 2 : sorted[mid]!;
}

function average(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function pct(values: number[], percentile: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.floor((percentile / 100) * sorted.length)));
  return sorted[index]!;
}

function sol(n: number, digits = 3): string {
  return `${n.toFixed(digits)} SOL`;
}

function compactNum(n: number, digits = 2): string {
  return Number.isFinite(n) ? n.toFixed(digits) : "0.00";
}

function percent(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

function classifySize(firstBuySize: number, maxBuySize: number, spent: number): string {
  if (maxBuySize >= 4.5 || spent >= 10) return "high conviction";
  if (maxBuySize >= 2 || spent >= 5) return "conviction";
  if (firstBuySize > 0 && firstBuySize <= 0.4 && spent < 2) return "probe";
  return "mid";
}

function classifyLadder(buyCount: number, intervals: number[]): string {
  if (buyCount <= 1) return "single entry";
  const medInterval = median(intervals);
  if (buyCount >= 8 && medInterval <= 20) return "rapid multi-ladder";
  if (buyCount >= 5) return "extended ladder";
  if (medInterval <= 10) return "quick add";
  return "measured add";
}

function buildCampaigns(data: SourceSummaryFile): CampaignModel[] {
  const rowsByMint = new Map<string, SourceRow[]>();
  for (const row of data.rows) {
    if (!row.mint) continue;
    const rows = rowsByMint.get(row.mint) ?? [];
    rows.push(row);
    rowsByMint.set(row.mint, rows);
  }

  return data.groups.map((group) => {
    const rows = sortByTs(rowsByMint.get(group.mint) ?? []);
    const buyRows = rows.filter((row) => row.side === "buy");
    const sellRows = rows.filter((row) => row.side === "sell");
    const firstBuy = buyRows[0];
    const buySizes = buyRows.map(rowBuySize).filter((n) => n > 0);
    const buyIntervalsSec = buyRows.slice(1).map((row, index) => row.ts - buyRows[index]!.ts);
    const sellIntervalsFromFirstBuySec =
      firstBuy === undefined ? [] : sellRows.map((row) => row.ts - firstBuy.ts);
    const firstTs = rows[0]?.ts ?? firstBuy?.ts ?? 0;
    const lastTs = rows[rows.length - 1]?.ts ?? firstTs;
    const maxBuySize = buySizes.length > 0 ? Math.max(...buySizes) : 0;
    const roi = group.spent > 0 ? group.net / group.spent : 0;

    return {
      group,
      rows,
      buyRows,
      sellRows,
      firstBuy,
      firstBuySize: buySizes[0] ?? 0,
      maxBuySize,
      buySizes,
      buyIntervalsSec,
      sellIntervalsFromFirstBuySec,
      durationSec: Math.max(0, lastTs - firstTs),
      isWinner: group.net > 0,
      isOpen: group.sells === 0 || Math.abs(group.openToken ?? 0) > 0.001,
      roi,
      sizeProfile: classifySize(buySizes[0] ?? 0, maxBuySize, group.spent),
      ladderProfile: classifyLadder(group.buys, buyIntervalsSec),
    };
  });
}

function bucketBy<T>(
  campaigns: CampaignModel[],
  labeler: (campaign: CampaignModel) => string
): Array<Record<string, string | number>> {
  const map = new Map<string, CampaignModel[]>();
  for (const campaign of campaigns) {
    const label = labeler(campaign);
    const bucket = map.get(label) ?? [];
    bucket.push(campaign);
    map.set(label, bucket);
  }

  return [...map.entries()]
    .map(([bucket, items]) => {
      const net = items.reduce((sum, item) => sum + item.group.net, 0);
      const spent = items.reduce((sum, item) => sum + item.group.spent, 0);
      const wins = items.filter((item) => item.isWinner).length;
      return {
        bucket,
        campaigns: items.length,
        wins,
        losses: items.length - wins,
        winRate: percent(wins / items.length),
        spent: compactNum(spent),
        net: compactNum(net),
        roi: percent(spent > 0 ? net / spent : 0),
      };
    })
    .sort((a, b) => Number(b.net) - Number(a.net));
}

function markdownTable(rows: Array<Record<string, string | number>>, columns: string[]): string {
  if (rows.length === 0) return "_No rows._";
  const header = `| ${columns.join(" | ")} |`;
  const divider = `| ${columns.map(() => "---").join(" | ")} |`;
  const body = rows.map((row) => `| ${columns.map((column) => String(row[column] ?? "")).join(" | ")} |`);
  return [header, divider, ...body].join("\n");
}

function topCampaignRows(campaigns: CampaignModel[], direction: "winners" | "losers"): Array<Record<string, string | number>> {
  const sorted = [...campaigns].sort((a, b) =>
    direction === "winners" ? b.group.net - a.group.net : a.group.net - b.group.net
  );
  return sorted.slice(0, 12).map((campaign) => ({
    mint: short(campaign.group.mint),
    buys: campaign.group.buys,
    sells: campaign.group.sells,
    spent: compactNum(campaign.group.spent),
    net: compactNum(campaign.group.net),
    roi: percent(campaign.roi),
    firstBuy: compactNum(campaign.firstBuySize, 3),
    maxBuy: compactNum(campaign.maxBuySize, 3),
    ladder: campaign.ladderProfile,
    holdSec: campaign.group.holdLastSec ?? "",
  }));
}

function deepSignalBucketRows(signals: DeepMintSignal[]): Array<Record<string, string | number>> {
  const buckets: Array<[string, (signal: DeepMintSignal) => boolean]> = [
    ["first wave: rank 1-2", (signal) => (signal.sourceBuyerRank ?? 9999) <= 2],
    ["early crowd: rank 3-25", (signal) => (signal.sourceBuyerRank ?? 0) > 2 && (signal.sourceBuyerRank ?? 9999) <= 25],
    ["late crowd: rank 26+", (signal) => (signal.sourceBuyerRank ?? 0) > 25],
    ["entry age <=10s", (signal) => (signal.sourceEntryAgeSec ?? 999999) <= 10],
    ["entry age 11-60s", (signal) => (signal.sourceEntryAgeSec ?? 0) > 10 && (signal.sourceEntryAgeSec ?? 999999) <= 60],
    ["entry age >60s", (signal) => (signal.sourceEntryAgeSec ?? 0) > 60],
    ["no pre-source actors", (signal) => signal.preSourceActors.length === 0],
    ["has pre-source actors", (signal) => signal.preSourceActors.length > 0],
  ];

  return buckets.map(([bucket, predicate]) => {
    const items = signals.filter(predicate);
    const wins = items.filter((item) => item.isWinner).length;
    const net = items.reduce((sum, item) => sum + item.net, 0);
    const avgRank = average(items.map((item) => item.sourceBuyerRank ?? 0).filter((rank) => rank > 0));
    return {
      bucket,
      campaigns: items.length,
      wins,
      losses: items.length - wins,
      winRate: percent(items.length > 0 ? wins / items.length : 0),
      net: compactNum(net),
      avgRank: compactNum(avgRank, 1),
    };
  });
}

function bucketByHistoryQuality(signals: DeepMintSignal[]): Array<Record<string, string | number>> {
  const map = new Map<string, DeepMintSignal[]>();
  for (const signal of signals) {
    const items = map.get(signal.historyQuality) ?? [];
    items.push(signal);
    map.set(signal.historyQuality, items);
  }

  return [...map.entries()]
    .map(([quality, items]) => {
      const wins = items.filter((item) => item.isWinner).length;
      const net = items.reduce((sum, item) => sum + item.net, 0);
      return {
        quality,
        campaigns: items.length,
        wins,
        losses: items.length - wins,
        winRate: percent(items.length > 0 ? wins / items.length : 0),
        net: compactNum(net),
      };
    })
    .sort((a, b) => String(a.quality).localeCompare(String(b.quality)));
}

function short(value: string, head = 6, tail = 4): string {
  return value.length <= head + tail ? value : `${value.slice(0, head)}...${value.slice(-tail)}`;
}

function standardSizeRows(campaigns: CampaignModel[]): Array<Record<string, string | number>> {
  const sizes = campaigns.flatMap((campaign) => campaign.buySizes);
  const buckets = new Map<string, number>();
  for (const size of sizes) {
    const key = size < 1 ? size.toFixed(3) : size.toFixed(2);
    buckets.set(key, (buckets.get(key) ?? 0) + 1);
  }
  return [...buckets.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12)
    .map(([size, count]) => ({ size, count }));
}

async function fetchParsedTransactions(signatures: string[]): Promise<ParsedTx[]> {
  if (!heliusEnhancedTransactionsUrl) return [];
  const parsed: ParsedTx[] = [];
  for (let i = 0; i < signatures.length; i += 100) {
    const batch = signatures.slice(i, i + 100);
    const response = await fetch(heliusEnhancedTransactionsUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ transactions: batch }),
      signal: AbortSignal.timeout(20000),
    });
    if (!response.ok) {
      const text = await response.text();
      if (response.status === 429 || text.includes("max usage reached") || text.includes("-32429")) {
        throw new HeliusQuotaError(`Helius quota reached: ${response.status} ${text.slice(0, 200)}`);
      }
      throw new Error(`Helius enhanced parse failed: ${response.status} ${text.slice(0, 200)}`);
    }
    parsed.push(...((await response.json()) as ParsedTx[]));
    if (i + 100 < signatures.length) await sleep(250);
  }
  return parsed;
}

async function fetchMintChainCache(
  campaign: CampaignModel,
  sourceWallet: string,
  options: { txLimit: number; accountTxLimit: number; lookbackSec: number; afterSec: number; refresh: boolean }
): Promise<MintChainCache> {
  await mkdir(CACHE_DIR, { recursive: true });
  const filePath = path.join(CACHE_DIR, `${campaign.group.mint}.json`);
  if (!options.refresh) {
    try {
      return JSON.parse(await readFile(filePath, "utf8")) as MintChainCache;
    } catch {
      // Cache miss.
    }
  }

  const firstBuyTs = campaign.firstBuy?.ts;
  const firstBuyParsed = campaign.firstBuy?.sig ? await fetchParsedTransactions([campaign.firstBuy.sig]) : [];
  const relatedAddresses = extractRelatedAddresses(campaign.group.mint, sourceWallet, firstBuyParsed[0]);
  const searchedAddresses = [campaign.group.mint, ...relatedAddresses].slice(0, 8);
  const seenSignatures = new Map<string, { signature: string; blockTime?: number | null; err?: unknown }>();

  for (const [index, address] of searchedAddresses.entries()) {
    const limit = index === 0 ? options.txLimit : options.accountTxLimit;
    const signatures = await readConnection.getSignaturesForAddress(new PublicKey(address), { limit });
    for (const sig of signatures) {
      if (firstBuyTs && sig.blockTime) {
        if (sig.blockTime < firstBuyTs - options.lookbackSec || sig.blockTime > firstBuyTs + options.afterSec) continue;
      }
      seenSignatures.set(sig.signature, { signature: sig.signature, blockTime: sig.blockTime, err: sig.err });
    }
    await sleep(150);
  }

  if (campaign.firstBuy?.sig) {
    seenSignatures.set(campaign.firstBuy.sig, { signature: campaign.firstBuy.sig, blockTime: firstBuyTs });
  }

  const windowed = [...seenSignatures.values()].sort((a, b) => (a.blockTime ?? 0) - (b.blockTime ?? 0));
  const parsedBySig = new Map<string, ParsedTx>();
  for (const tx of firstBuyParsed) parsedBySig.set(tx.signature, tx);
  const missing = windowed.map((sig) => sig.signature).filter((sig) => !parsedBySig.has(sig));
  for (const tx of await fetchParsedTransactions(missing)) parsedBySig.set(tx.signature, tx);
  const parsed = [...parsedBySig.values()];
  const cache = {
    mint: campaign.group.mint,
    fetchedAt: new Date().toISOString(),
    searchedAddresses,
    signatures: windowed.map((sig) => ({ signature: sig.signature, blockTime: sig.blockTime, err: sig.err })),
    parsed,
  };
  await writeFile(filePath, JSON.stringify(cache, null, 2));
  await sleep(250);
  return cache;
}

function extractRelatedAddresses(mint: string, sourceWallet: string, tx: ParsedTx | undefined): string[] {
  const addresses = new Set<string>();
  if (!tx) return [];

  for (const account of tx.accountData ?? []) {
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.mint !== mint) continue;
      if (change.userAccount === sourceWallet) continue;
      if (isUsefulActor(account.account, sourceWallet, mint)) addresses.add(account.account);
      if (isUsefulActor(change.tokenAccount, sourceWallet, mint)) addresses.add(change.tokenAccount);
    }
  }

  return [...addresses];
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isUsefulActor(actor: string | undefined, sourceWallet: string, mint: string): actor is string {
  if (!actor) return false;
  if (actor === sourceWallet || actor === mint || actor === WRAPPED_SOL_MINT) return false;
  try {
    new PublicKey(actor);
    return true;
  } catch {
    return false;
  }
}

function tokenDelta(change: { rawTokenAmount?: { tokenAmount?: string; decimals?: number } }): number {
  const raw = Number(change.rawTokenAmount?.tokenAmount ?? 0);
  const decimals = Number(change.rawTokenAmount?.decimals ?? 0);
  if (!Number.isFinite(raw)) return 0;
  return raw / 10 ** decimals;
}

function actorsForMintTx(tx: ParsedTx, mint: string, sourceWallet: string): string[] {
  const actors = new Set<string>();
  if (isUsefulActor(tx.feePayer, sourceWallet, mint)) actors.add(tx.feePayer);

  for (const transfer of tx.tokenTransfers ?? []) {
    if (transfer.mint !== mint) continue;
    if (isUsefulActor(transfer.toUserAccount, sourceWallet, mint)) actors.add(transfer.toUserAccount);
    if (isUsefulActor(transfer.fromUserAccount, sourceWallet, mint)) actors.add(transfer.fromUserAccount);
  }

  for (const account of tx.accountData ?? []) {
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.mint !== mint) continue;
      if (Math.abs(tokenDelta(change)) <= 0) continue;
      if (isUsefulActor(change.userAccount, sourceWallet, mint)) actors.add(change.userAccount);
    }
  }

  return [...actors];
}

function buildDeepMintSignal(campaign: CampaignModel, cache: MintChainCache, sourceWallet: string): DeepMintSignal {
  const firstBuyTs = campaign.firstBuy?.ts ?? 0;
  const parsed = [...cache.parsed].filter((tx) => tx.timestamp).sort((a, b) => (a.timestamp ?? 0) - (b.timestamp ?? 0));
  const earliestSeen = parsed.find((tx) => (tx.timestamp ?? 0) <= firstBuyTs);
  const preSource = parsed.filter((tx) => (tx.timestamp ?? 0) < firstBuyTs);
  const actorEvents: Array<{ actor: string; ts: number }> = [];

  for (const tx of preSource) {
    const ts = tx.timestamp ?? 0;
    for (const actor of actorsForMintTx(tx, campaign.group.mint, sourceWallet)) {
      actorEvents.push({ actor, ts });
    }
  }

  const preSourceActors = [...new Set(actorEvents.map((event) => event.actor))];
  const preSourceActorLeadSec: Record<string, number> = {};
  for (const actor of preSourceActors) {
    const latestPreSource = actorEvents
      .filter((event) => event.actor === actor)
      .sort((a, b) => b.ts - a.ts)[0];
    if (latestPreSource) preSourceActorLeadSec[actor] = firstBuyTs - latestPreSource.ts;
  }
  const closestPreSourceActors = [...actorEvents]
    .sort((a, b) => b.ts - a.ts)
    .map((event) => event.actor)
    .filter((actor, index, all) => all.indexOf(actor) === index)
    .slice(0, 5);

  return {
    mint: campaign.group.mint,
    net: campaign.group.net,
    isWinner: campaign.isWinner,
    firstBuyTs,
    earliestSeenTs: earliestSeen?.timestamp,
    sourceEntryAgeSec: earliestSeen?.timestamp ? firstBuyTs - earliestSeen.timestamp : undefined,
    sourceBuyerRank: preSourceActors.length + 1,
    preSourceTxCount: preSource.length,
    preSourceActors,
    preSourceActorLeadSec,
    closestPreSourceActors,
    possibleCreator:
      earliestSeen?.timestamp && earliestSeen.timestamp < firstBuyTs && earliestSeen.feePayer !== sourceWallet
        ? earliestSeen.feePayer
        : undefined,
    sourceFirstBuySignature: campaign.firstBuy?.sig,
    historyQuality:
      campaign.firstBuy?.sig && parsed.some((tx) => tx.signature === campaign.firstBuy?.sig) && preSource.length > 0
        ? "good"
        : parsed.some((tx) => tx.signature === campaign.firstBuy?.sig)
        ? "partial"
        : "poor",
  };
}

async function runDeep(campaigns: CampaignModel[], sourceWallet: string): Promise<DeepRunResult> {
  const txLimit = numberArg("--tx-limit", 1000);
  const accountTxLimit = numberArg("--account-tx-limit", 500);
  const lookbackSec = numberArg("--lookback-sec", 900);
  const afterSec = numberArg("--after-sec", 120);
  const maxMints = numberArg("--max-mints", campaigns.length);
  const refresh = hasFlag("--refresh");
  const selected = campaigns
    .filter((campaign) => campaign.firstBuy)
    .sort((a, b) => Math.abs(b.group.net) - Math.abs(a.group.net))
    .slice(0, maxMints);

  const mintSignals: DeepMintSignal[] = [];
  let stopReason: string | undefined;
  for (const [index, campaign] of selected.entries()) {
    console.log(`Deep signal ${index + 1}/${selected.length}: ${short(campaign.group.mint)} net=${compactNum(campaign.group.net)}`);
    try {
      const cache = await fetchMintChainCache(campaign, sourceWallet, { txLimit, accountTxLimit, lookbackSec, afterSec, refresh });
      mintSignals.push(buildDeepMintSignal(campaign, cache, sourceWallet));
    } catch (err) {
      if (err instanceof HeliusQuotaError) {
        stopReason = err.message;
        console.warn(`Stopping deep fetch early: ${err.message}`);
        break;
      }
      throw err;
    }
  }

  const leadMap = new Map<string, { campaigns: Set<string>; wins: number; losses: number; net: number; leadSecs: number[]; examples: string[] }>();
  for (const signal of mintSignals) {
    for (const actor of signal.preSourceActors) {
      const entry = leadMap.get(actor) ?? {
        campaigns: new Set<string>(),
        wins: 0,
        losses: 0,
        net: 0,
        leadSecs: [],
        examples: [],
      };
      if (!entry.campaigns.has(signal.mint)) {
        entry.campaigns.add(signal.mint);
        if (signal.isWinner) entry.wins += 1;
        else entry.losses += 1;
        entry.net += signal.net;
        if (entry.examples.length < 4) entry.examples.push(short(signal.mint));
      }
      const leadSec = signal.preSourceActorLeadSec[actor];
      if (leadSec !== undefined) entry.leadSecs.push(leadSec);
      leadMap.set(actor, entry);
    }
  }

  const leadActors = [...leadMap.entries()]
    .map(([actor, entry]) => ({
      actor,
      campaigns: entry.campaigns.size,
      wins: entry.wins,
      losses: entry.losses,
      net: entry.net,
      avgLeadSec: average(entry.leadSecs),
      examples: entry.examples,
    }))
    .filter((entry) => entry.campaigns >= 2)
    .sort((a, b) => b.campaigns - a.campaigns || b.net - a.net)
    .slice(0, 30);

  const creatorMap = new Map<string, DeepMintSignal[]>();
  for (const signal of mintSignals) {
    if (!signal.possibleCreator) continue;
    const items = creatorMap.get(signal.possibleCreator) ?? [];
    items.push(signal);
    creatorMap.set(signal.possibleCreator, items);
  }
  const creators = [...creatorMap.entries()]
    .map(([creator, items]) => {
      const wins = items.filter((item) => item.isWinner).length;
      const net = items.reduce((sum, item) => sum + item.net, 0);
      return {
        creator: short(creator),
        campaigns: items.length,
        wins,
        losses: items.length - wins,
        winRate: percent(wins / items.length),
        net: compactNum(net),
        examples: items.slice(0, 4).map((item) => short(item.mint)).join(", "),
      };
    })
    .filter((row) => Number(row.campaigns) >= 2)
    .sort((a, b) => Number(b.campaigns) - Number(a.campaigns) || Number(b.net) - Number(a.net))
    .slice(0, 20);

  return {
    mintSignals,
    leadActors,
    creators,
    requestedMints: selected.length,
    completedMints: mintSignals.length,
    stoppedEarly: stopReason !== undefined,
    stopReason,
  };
}

function buildReport(data: SourceSummaryFile, campaigns: CampaignModel[], deep?: Awaited<ReturnType<typeof runDeep>>): string {
  const summary = data.summary;
  const winners = campaigns.filter((campaign) => campaign.isWinner);
  const losers = campaigns.filter((campaign) => !campaign.isWinner);
  const closed = campaigns.filter((campaign) => !campaign.isOpen);
  const allBuySizes = campaigns.flatMap((campaign) => campaign.buySizes);
  const allBuyIntervals = campaigns.flatMap((campaign) => campaign.buyIntervalsSec).filter((n) => n >= 0);
  const firstSellTimes = campaigns.flatMap((campaign) => campaign.sellIntervalsFromFirstBuySec).filter((n) => n >= 0);

  const lines: string[] = [
    "# Signal Investigation Report",
    "",
    `Wallet: \`${summary.wallet}\``,
    `Generated: ${new Date().toISOString()}`,
    "",
    "## Executive Read",
    "",
    `The sampled wallet is profitable in the captured window: ${summary.wins} wins, ${summary.losses} losses, ${percent(summary.winRate)} win rate, ${sol(summary.net)} net on ${sol(summary.totalSpent)} spent.`,
    `The execution profile is highly patterned: ${summary.buySize?.topBuckets?.slice(0, 4).map(([size, count]) => `${size} SOL x${count}`).join(", ") ?? "fixed-size buys"} dominate the ladder.`,
    "The edge is likely a two-part system: a discovery/filter signal that chooses mints, then a fast fixed-size laddering and partial-exit execution system.",
    "",
    "## Core Metrics",
    "",
    markdownTable(
      [
        { metric: "campaigns", value: campaigns.length },
        { metric: "closed campaigns", value: closed.length },
        { metric: "open or residue campaigns", value: campaigns.length - closed.length },
        { metric: "median buy size", value: sol(median(allBuySizes)) },
        { metric: "p75 buy size", value: sol(pct(allBuySizes, 75)) },
        { metric: "median add interval", value: `${compactNum(median(allBuyIntervals), 1)} sec` },
        { metric: "median first sell delay", value: `${compactNum(median(firstSellTimes), 1)} sec` },
        { metric: "median winner ROI", value: percent(median(winners.map((campaign) => campaign.roi))) },
        { metric: "median loser ROI", value: percent(median(losers.map((campaign) => campaign.roi))) },
      ],
      ["metric", "value"]
    ),
    "",
    "## Standard Buy Sizes",
    "",
    markdownTable(standardSizeRows(campaigns), ["size", "count"]),
    "",
    "## By Size Profile",
    "",
    markdownTable(bucketBy(campaigns, (campaign) => campaign.sizeProfile), [
      "bucket",
      "campaigns",
      "wins",
      "losses",
      "winRate",
      "spent",
      "net",
      "roi",
    ]),
    "",
    "## By Ladder Profile",
    "",
    markdownTable(bucketBy(campaigns, (campaign) => campaign.ladderProfile), [
      "bucket",
      "campaigns",
      "wins",
      "losses",
      "winRate",
      "spent",
      "net",
      "roi",
    ]),
    "",
    "## Top Winners",
    "",
    markdownTable(topCampaignRows(campaigns, "winners"), [
      "mint",
      "buys",
      "sells",
      "spent",
      "net",
      "roi",
      "firstBuy",
      "maxBuy",
      "ladder",
      "holdSec",
    ]),
    "",
    "## Top Losers",
    "",
    markdownTable(topCampaignRows(campaigns, "losers"), [
      "mint",
      "buys",
      "sells",
      "spent",
      "net",
      "roi",
      "firstBuy",
      "maxBuy",
      "ladder",
      "holdSec",
    ]),
    "",
    "## Working Strategy Model",
    "",
    "1. Treat 0.265 SOL as the scout/probe unit. It appears far too often to be discretionary sizing.",
    "2. Treat roughly 3.02 SOL and 5.02 SOL as conviction units. These are likely triggered after the token passes a confirmation threshold.",
    "3. First sells often occur within seconds, so exits are part of the signal. Copying only buys would miss the strategy.",
    "4. The biggest winners are not single entries; they are rapid multi-ladders where he keeps adding while the trade is working.",
    "5. The hard edge to uncover is the confirmation threshold that upgrades a token from probe to conviction.",
  ];

  if (deep) {
    lines.push(
      "",
      "## Deep Chain Attribution",
      "",
      `Deep mode analyzed ${deep.completedMints}/${deep.requestedMints} requested mints with cached mint-address history.${deep.stoppedEarly ? ` Stopped early: ${deep.stopReason ?? "quota or fetch limit reached"}.` : ""}`,
      "",
      "### Signal Tests",
      "",
      markdownTable(deepSignalBucketRows(deep.mintSignals), [
        "bucket",
        "campaigns",
        "wins",
        "losses",
        "winRate",
        "net",
        "avgRank",
      ]),
      "",
      "### History Quality",
      "",
      markdownTable(
        bucketByHistoryQuality(deep.mintSignals),
        ["quality", "campaigns", "wins", "losses", "winRate", "net"]
      ),
      "",
      "### Recurring Pre-Source Actors",
      "",
      markdownTable(
        deep.leadActors.slice(0, 15).map((actor) => ({
          actor: short(actor.actor),
          campaigns: actor.campaigns,
          wins: actor.wins,
          losses: actor.losses,
          net: compactNum(actor.net),
          avgLeadSec: compactNum(actor.avgLeadSec, 1),
          examples: actor.examples.join(", "),
        })),
        ["actor", "campaigns", "wins", "losses", "net", "avgLeadSec", "examples"]
      ),
      "",
      "### Repeated Possible Creators",
      "",
      markdownTable(deep.creators, ["creator", "campaigns", "wins", "losses", "winRate", "net", "examples"]),
      "",
      "### Entry-Age Snapshot",
      "",
      markdownTable(
        deep.mintSignals
          .filter((signal) => signal.sourceEntryAgeSec !== undefined)
          .sort((a, b) => Math.abs(b.net) - Math.abs(a.net))
          .slice(0, 20)
          .map((signal) => ({
            mint: short(signal.mint),
            net: compactNum(signal.net),
            entryAgeSec: signal.sourceEntryAgeSec ?? "",
            buyerRank: signal.sourceBuyerRank ?? "",
            preActors: signal.preSourceActors.length,
            closestActors: signal.closestPreSourceActors.map((actor) => short(actor)).join(", "),
          })),
        ["mint", "net", "entryAgeSec", "buyerRank", "preActors", "closestActors"]
      )
    );
  } else {
    lines.push(
      "",
      "## Deep Chain Attribution",
      "",
      "Deep mode was not run. Run `npm run investigate:signal -- --deep` to fetch mint-level pre-buy histories and rank recurring lead actors/creators."
    );
  }

  lines.push(
    "",
    "## Next Tests",
    "",
    "1. Run deep mode over all 98 mints and inspect recurring pre-source actors.",
    "2. Add a live shadow mode that detects his first probe and records what happened in the preceding 60 seconds.",
    "3. Backtest copy latency: 1s, 3s, 5s, 10s after his buy, with your intended SOL size.",
    "4. Backtest a safer strategy that copies only conviction adds after a successful probe instead of copying every tiny entry.",
    ""
  );

  return lines.join("\n");
}

async function main(): Promise<void> {
  const data = await loadJson<SourceSummaryFile>(SUMMARY_PATH);
  const campaigns = buildCampaigns(data);
  const deep = hasFlag("--deep") ? await runDeep(campaigns, data.summary.wallet) : undefined;
  const report = buildReport(data, campaigns, deep);
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: data.summary.wallet,
    summary: data.summary,
    campaigns: campaigns.map((campaign) => ({
      mint: campaign.group.mint,
      net: campaign.group.net,
      spent: campaign.group.spent,
      buys: campaign.group.buys,
      sells: campaign.group.sells,
      firstBuySize: campaign.firstBuySize,
      maxBuySize: campaign.maxBuySize,
      buySizes: campaign.buySizes,
      buyIntervalsSec: campaign.buyIntervalsSec,
      sellIntervalsFromFirstBuySec: campaign.sellIntervalsFromFirstBuySec,
      sizeProfile: campaign.sizeProfile,
      ladderProfile: campaign.ladderProfile,
      roi: campaign.roi,
    })),
    deep,
  };

  await writeFile(REPORT_PATH, report);
  await writeFile(OUTPUT_PATH, JSON.stringify(output, null, 2));
  console.log(`Wrote ${REPORT_PATH}`);
  console.log(`Wrote ${OUTPUT_PATH}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exitCode = 1;
});
