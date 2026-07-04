import "dotenv/config";
import { readFileSync } from "node:fs";
import { mkdir, readFile, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { HeliusKeyPool, heliusKeysFromEnv } from "./heliusKeyPool.js";

type LedgerRow = {
  mint: string;
  route: "pump" | "bags" | "other";
  entrySignature?: string;
  entryTs: number;
  entryIso: string;
  entrySolUsd: number;
  firstBuySol: number;
  entryMarketCapUsd: number;
  sourceExitRoiPct: number;
  realizedPnlSol: number;
  holdMinutes: number;
};

type LedgerReport = {
  wallet: string;
  coverage: Record<string, unknown>;
  rows: LedgerRow[];
};

type TokenBalanceChange = {
  userAccount?: string;
  mint?: string;
  rawTokenAmount?: { tokenAmount?: string; decimals?: number };
};

export type HeliusTx = {
  signature: string;
  timestamp?: number;
  feePayer?: string;
  nativeTransfers?: Array<{
    fromUserAccount?: string;
    toUserAccount?: string;
    amount?: number;
  }>;
  accountData?: Array<{
    account: string;
    nativeBalanceChange?: number;
    tokenBalanceChanges?: TokenBalanceChange[];
  }>;
};

export type FlowEvent = {
  signature: string;
  timestamp: number;
  side: "buy" | "sell";
  wallet: string;
  sol: number;
  tokens: number;
  marketCapUsd: number | null;
};

export type WindowFeatures = {
  seconds: number;
  buys: number;
  sells: number;
  uniqueWallets: number;
  buyVolumeSol: number;
  sellVolumeSol: number;
  netBuySol: number;
  maxBuySol: number;
  medianBuySol: number | null;
  firstMarketCapUsd: number | null;
  latestMarketCapUsd: number | null;
  marketCapMomentumPct: number | null;
};

type Profile = {
  name: string;
  minScore: number;
  maxScore: number;
  minMc: number;
  maxMc: number;
  minSource: number;
  maxSource: number;
  maxWallets: number;
  maxBuys: number;
  maxSells: number;
  maxBuyVolume: number;
};

type CandidateEvaluation = {
  event: FlowEvent;
  score: number;
  profileScores: Record<string, number>;
  knownPrecursor: boolean;
  context: WindowFeatures;
  profileGates: Record<string, string[]>;
};

type AuditRow = {
  ledger: LedgerRow;
  outcome: "loss" | "small_win" | "scalp_win" | "runner";
  fetchStatus: "measured" | "no_pre_entry_flow" | "history_truncated" | "fetch_error";
  fetchedTransactions: number;
  oldestFetchedTs: number | null;
  events: number;
  firstPreEntryLeadSeconds: number | null;
  windows: Record<string, WindowFeatures>;
  candidates: CandidateEvaluation[];
  error?: string;
};

const WSOL = "So11111111111111111111111111111111111111112";
const SUPPLY = Number(process.env.PREBUY_INFERRED_TOKEN_SUPPLY ?? "1000000000");
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-pre-entry-cache");
const LOOKBACK_SECONDS = Number(process.env.WALLET_PREENTRY_LOOKBACK_SECONDS ?? "60");
const MAX_MINT_TXS_DEFAULT = Number(process.env.WALLET_PREENTRY_MAX_MINT_TXS ?? "1000");
const PAGE_SLEEP_MS = Number(process.env.WALLET_PREENTRY_PAGE_SLEEP_MS ?? "350");
const MINT_SLEEP_MS = Number(process.env.WALLET_PREENTRY_MINT_SLEEP_MS ?? "250");
const WINDOWS = [1, 3, 5, 10, 30, 60];

const HELIUS_KEYS = heliusKeysFromEnv();
const HELIUS_POOL = new HeliusKeyPool(HELIUS_KEYS, {
  cooldownMs: Number(process.env.HELIUS_KEY_COOLDOWN_MS ?? "60000"),
  maxAttempts: Number(
    process.env.HELIUS_KEY_MAX_ATTEMPTS ?? String(Math.max(12, HELIUS_KEYS.length * 5))
  ),
  onEvent: (event) => {
    if (event.event === "key_selected") return;
    console.warn(
      `[helius] ${event.event} keySlot=${event.keySlot}` +
        (event.status ? ` status=${event.status}` : "")
    );
  },
});

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function asNumber(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function median(values: number[]): number | null {
  const rows = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!rows.length) return null;
  const mid = Math.floor(rows.length / 2);
  return rows.length % 2 ? rows[mid]! : (rows[mid - 1]! + rows[mid]!) / 2;
}

function average(values: number[]): number | null {
  const rows = values.filter(Number.isFinite);
  return rows.length ? rows.reduce((sum, value) => sum + value, 0) / rows.length : null;
}

function profitFactor(values: number[]): number | null {
  const positive = values.filter((value) => value > 0).reduce((sum, value) => sum + value, 0);
  const negative = Math.abs(values.filter((value) => value < 0).reduce((sum, value) => sum + value, 0));
  return negative > 0 ? positive / negative : positive > 0 ? null : 0;
}

function percentage(value: number, total: number): number {
  return (value / Math.max(1, total)) * 100;
}

function fmtPct(value: number | null | undefined): string {
  return value == null ? "-" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtUsd(value: number | null | undefined): string {
  return value == null ? "-" : `$${Math.round(value).toLocaleString("en-US")}`;
}

function outcome(row: LedgerRow): AuditRow["outcome"] {
  if (row.sourceExitRoiPct < 0) return "loss";
  if (row.sourceExitRoiPct >= 50) return "runner";
  if (row.sourceExitRoiPct >= 10) return "scalp_win";
  return "small_win";
}

async function readJson<T>(file: string): Promise<T | null> {
  try {
    return JSON.parse((await readFile(file, "utf8")).replace(/^\uFEFF/, "")) as T;
  } catch {
    return null;
  }
}

async function writeJson(file: string, value: unknown): Promise<void> {
  await mkdir(path.dirname(file), { recursive: true });
  await writeFile(file, JSON.stringify(value), "utf8");
}

async function fetchAddressPage(
  mint: string,
  limit: number,
  before?: string
): Promise<HeliusTx[]> {
  const response = await HELIUS_POOL.request(
    (apiKey) => {
      const params = new URLSearchParams({ "api-key": apiKey, limit: String(limit) });
      if (before) params.set("before", before);
      return `https://api-mainnet.helius-rpc.com/v0/addresses/${mint}/transactions?${params}`;
    },
    { signal: AbortSignal.timeout(20_000) }
  );
  if (!response.ok) {
    throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 180)}`);
  }
  return (await response.json()) as HeliusTx[];
}

async function fetchMintHistory(
  mint: string,
  entryTs: number,
  maxTxs: number
): Promise<{ txs: HeliusTx[]; reachedWindow: boolean }> {
  const cacheKey = `${mint}-${entryTs}-${maxTxs}.json`;
  const cachePath = path.join(CACHE_DIR, cacheKey);
  const cached = await readJson<{ txs: HeliusTx[]; reachedWindow: boolean }>(cachePath);
  if (cached) return cached;
  const partialPath = `${cachePath}.partial`;
  const partial = await readJson<{ txs: HeliusTx[]; reachedWindow: boolean }>(partialPath);
  const txs = partial?.txs ?? [];
  let before = txs[txs.length - 1]?.signature;
  let reachedWindow =
    partial?.reachedWindow ??
    txs.some((tx) => (tx.timestamp ?? Infinity) <= entryTs - LOOKBACK_SECONDS);

  while (txs.length < maxTxs && !reachedWindow) {
    const batch = await fetchAddressPage(mint, Math.min(100, maxTxs - txs.length), before);
    if (!batch.length) {
      reachedWindow = true;
      break;
    }
    txs.push(...batch);
    before = batch[batch.length - 1]?.signature;
    reachedWindow = batch.some(
      (tx) => (tx.timestamp ?? Infinity) <= entryTs - LOOKBACK_SECONDS
    );
    await writeJson(partialPath, { txs, reachedWindow });
    await sleep(PAGE_SLEEP_MS);
  }
  const result = { txs, reachedWindow };
  await writeJson(cachePath, result);
  await unlink(partialPath).catch(() => undefined);
  return result;
}

export function extractFlowEvent(
  tx: HeliusTx,
  mint: string,
  solUsd: number
): FlowEvent | null {
  const nativeByAccount = new Map<string, number>();
  const transferredByAccount = new Map<string, number>();
  const tokenByOwner = new Map<string, { raw: number; decimals: number }>();
  const wsolByOwner = new Map<string, number>();

  for (const transfer of tx.nativeTransfers ?? []) {
    const amount = transfer.amount ?? 0;
    if (transfer.fromUserAccount) {
      transferredByAccount.set(
        transfer.fromUserAccount,
        (transferredByAccount.get(transfer.fromUserAccount) ?? 0) - amount
      );
    }
    if (transfer.toUserAccount) {
      transferredByAccount.set(
        transfer.toUserAccount,
        (transferredByAccount.get(transfer.toUserAccount) ?? 0) + amount
      );
    }
  }

  for (const account of tx.accountData ?? []) {
    nativeByAccount.set(
      account.account,
      (nativeByAccount.get(account.account) ?? 0) + (account.nativeBalanceChange ?? 0)
    );
    for (const change of account.tokenBalanceChanges ?? []) {
      const owner = change.userAccount;
      if (!owner || !change.mint) continue;
      const raw = asNumber(change.rawTokenAmount?.tokenAmount);
      if (raw == null || raw === 0) continue;
      if (change.mint === mint) {
        const current = tokenByOwner.get(owner) ?? {
          raw: 0,
          decimals: change.rawTokenAmount?.decimals ?? 0,
        };
        current.raw += raw;
        current.decimals = change.rawTokenAmount?.decimals ?? current.decimals;
        tokenByOwner.set(owner, current);
      } else if (change.mint === WSOL) {
        wsolByOwner.set(owner, (wsolByOwner.get(owner) ?? 0) + raw);
      }
    }
  }

  const candidates = [...tokenByOwner.entries()]
    .map(([wallet, token]) => {
      const wrappedQuote = wsolByOwner.get(wallet) ?? 0;
      const transferredQuote = transferredByAccount.get(wallet) ?? 0;
      const quoteLamports =
        wrappedQuote !== 0
          ? wrappedQuote
          : transferredQuote !== 0
            ? transferredQuote
            : nativeByAccount.get(wallet) ?? 0;
      const opposite =
        (token.raw > 0 && quoteLamports < 0) || (token.raw < 0 && quoteLamports > 0);
      return {
        wallet,
        token,
        quoteLamports,
        rankTier: (wallet === tx.feePayer ? 2 : 0) + (opposite ? 1 : 0),
      };
    })
    .filter((row) => row.quoteLamports !== 0)
    .sort(
      (a, b) =>
        b.rankTier - a.rankTier ||
        Math.abs(b.quoteLamports) - Math.abs(a.quoteLamports)
    );
  const selected = candidates[0];
  if (!selected) return null;
  const isBuy = selected.token.raw > 0 && selected.quoteLamports < 0;
  const isSell = selected.token.raw < 0 && selected.quoteLamports > 0;
  if (!isBuy && !isSell) return null;
  const sol = Math.abs(selected.quoteLamports) / 1_000_000_000;
  const tokens = Math.abs(selected.token.raw) / 10 ** selected.token.decimals;
  const marketCapUsd =
    tokens > 0 && sol > 0 ? ((sol * solUsd) / tokens) * SUPPLY : null;
  return {
    signature: tx.signature,
    timestamp: tx.timestamp ?? 0,
    side: isBuy ? "buy" : "sell",
    wallet: selected.wallet,
    sol,
    tokens,
    marketCapUsd:
      marketCapUsd != null && Number.isFinite(marketCapUsd) ? marketCapUsd : null,
  };
}

export function windowFeatures(
  events: FlowEvent[],
  targetTs: number,
  seconds: number
): WindowFeatures {
  const rows = events.filter(
    (event) => event.timestamp >= targetTs - seconds && event.timestamp < targetTs
  );
  const buys = rows.filter((event) => event.side === "buy");
  const sells = rows.filter((event) => event.side === "sell");
  const marketCaps = rows
    .filter((event) => event.marketCapUsd != null)
    .map((event) => event.marketCapUsd!);
  const firstMc = marketCaps[0] ?? null;
  const latestMc = marketCaps[marketCaps.length - 1] ?? null;
  return {
    seconds,
    buys: buys.length,
    sells: sells.length,
    uniqueWallets: new Set(rows.map((event) => event.wallet)).size,
    buyVolumeSol: buys.reduce((sum, event) => sum + event.sol, 0),
    sellVolumeSol: sells.reduce((sum, event) => sum + event.sol, 0),
    netBuySol:
      buys.reduce((sum, event) => sum + event.sol, 0) -
      sells.reduce((sum, event) => sum + event.sol, 0),
    maxBuySol: Math.max(0, ...buys.map((event) => event.sol)),
    medianBuySol: median(buys.map((event) => event.sol)),
    firstMarketCapUsd: firstMc,
    latestMarketCapUsd: latestMc,
    marketCapMomentumPct:
      firstMc != null && latestMc != null && firstMc > 0
        ? ((latestMc / firstMc) - 1) * 100
        : null,
  };
}

function parseEnv(text: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const separator = line.indexOf("=");
    if (separator <= 0) continue;
    values[line.slice(0, separator).trim()] = line.slice(separator + 1).trim();
  }
  return values;
}

function envNumber(env: Record<string, string>, key: string, fallback: number): number {
  const parsed = Number(env[key]);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function loadProfiles(env: Record<string, string>): Profile[] {
  return [
    {
      name: "current-edge-env",
      minScore: envNumber(env, "LAUNCH_MIN_SCORE", 85),
      maxScore: envNumber(env, "LAUNCH_MAX_SCORE", 94),
      minMc: envNumber(env, "LAUNCH_MIN_ENTRY_MC_USD", 2_000),
      maxMc: envNumber(env, "LAUNCH_MAX_ENTRY_MC_USD", 4_000),
      minSource: envNumber(env, "LAUNCH_MIN_SOURCE_BUY_SOL", 0.25),
      maxSource: envNumber(env, "LAUNCH_MAX_SOURCE_BUY_SOL", 1.24),
      maxWallets: envNumber(env, "LAUNCH_MAX_UNIQUE_WALLETS_60", 12),
      maxBuys: envNumber(env, "LAUNCH_MAX_BUY_COUNT_60", 8),
      maxSells: envNumber(env, "LAUNCH_MAX_SELL_COUNT_60", 8),
      maxBuyVolume: envNumber(env, "LAUNCH_MAX_BUY_VOLUME_SOL_60", 25),
    },
    {
      name: "runner-aggregate",
      minScore: 85,
      maxScore: 94,
      minMc: 2_000,
      maxMc: 4_000,
      minSource: 0.25,
      maxSource: 1.24,
      maxWallets: 12,
      maxBuys: 8,
      maxSells: 8,
      maxBuyVolume: 25,
    },
    {
      name: "wide-scout",
      minScore: 80,
      maxScore: 89,
      minMc: 1_500,
      maxMc: 9_000,
      minSource: 0.2,
      maxSource: 1.5,
      maxWallets: 15,
      maxBuys: 25,
      maxSells: 8,
      maxBuyVolume: 25,
    },
  ];
}

export function scoreEvent(
  event: FlowEvent,
  context: WindowFeatures,
  knownPrecursor: boolean,
  isPumpRoute: boolean,
  maxBuys: number,
  maxSells: number,
  maxBuyVolume: number
): number {
  let score = isPumpRoute ? 10 : 0;
  const mc = event.marketCapUsd ?? 0;
  if (mc >= 1_500 && mc < 3_000) score += 18;
  else if (mc < 5_000) score += 16;
  else if (mc < 10_000) score += 12;
  else score -= 12;
  if (event.sol >= 0.25 && event.sol <= 1.5) score += 16;
  else if (event.sol > 1.5 && event.sol <= 3) score += 8;
  else if (event.sol < 0.05) score -= 10;
  if (knownPrecursor) score += 24;
  if (context.buys >= 2 && context.buys <= 12) score += 12;
  else if (context.buys > maxBuys) score -= 14;
  if (context.uniqueWallets >= 2 && context.uniqueWallets <= 10) score += 10;
  if (context.sells > 0 && context.netBuySol <= 0) score -= 20;
  if (context.sells > maxSells) score -= 18;
  if (context.buyVolumeSol > maxBuyVolume) score -= 12;
  return Math.max(0, Math.min(100, Math.round(score)));
}

function profileGates(
  event: FlowEvent,
  score: number,
  context: WindowFeatures,
  profile: Profile,
  isPumpRoute: boolean
): string[] {
  const gates: string[] = [];
  if (!isPumpRoute) gates.push("route");
  if (event.marketCapUsd == null) gates.push("entry_mc_missing");
  else if (event.marketCapUsd < profile.minMc) gates.push("entry_mc_low");
  else if (event.marketCapUsd >= profile.maxMc) gates.push("entry_mc_high");
  if (event.sol < profile.minSource) gates.push("source_buy_low");
  else if (event.sol > profile.maxSource) gates.push("source_buy_high");
  if (score < profile.minScore) gates.push("score_low");
  else if (score > profile.maxScore) gates.push("score_high");
  if (context.uniqueWallets > profile.maxWallets) gates.push("wallet_crowding");
  if (context.buys > profile.maxBuys) gates.push("buy_crowding");
  if (context.sells > profile.maxSells) gates.push("sell_crowding");
  if (context.buyVolumeSol > profile.maxBuyVolume) gates.push("buy_volume");
  return gates;
}

export function stratifiedSample(rows: LedgerRow[], limit: number): LedgerRow[] {
  const sorted = [...rows].sort((a, b) => b.entryTs - a.entryTs);
  const groups = new Map<AuditRow["outcome"], LedgerRow[]>([
    ["loss", []],
    ["small_win", []],
    ["scalp_win", []],
    ["runner", []],
  ]);
  for (const row of sorted) groups.get(outcome(row))!.push(row);
  const selected: LedgerRow[] = [];
  const selectedKeys = new Set<string>();
  const quota = Math.max(1, Math.floor(limit / groups.size));
  for (const rowsInGroup of groups.values()) {
    for (const row of rowsInGroup.slice(0, quota)) {
      selected.push(row);
      selectedKeys.add(`${row.mint}:${row.entryTs}`);
    }
  }
  for (const row of sorted) {
    if (selected.length >= limit) break;
    const key = `${row.mint}:${row.entryTs}`;
    if (selectedKeys.has(key)) continue;
    selected.push(row);
    selectedKeys.add(key);
  }
  return selected.sort((a, b) => b.entryTs - a.entryTs);
}

function loadPrecursors(edgeRoot: string): Set<string> {
  try {
    const rows = JSON.parse(
      readFileSync(path.join(edgeRoot, "data", "lead-wallets.json"), "utf8")
    ) as Array<{ wallet?: string; uniqueMints?: number; medianLeadSeconds?: number }>;
    return new Set(
      rows
        .filter(
          (row) =>
            row.wallet &&
            (row.uniqueMints ?? 0) >= 2 &&
            (row.medianLeadSeconds ?? Infinity) <= 15
        )
        .map((row) => row.wallet!)
    );
  } catch {
    return new Set();
  }
}

function groupFeatures(rows: AuditRow[], group: AuditRow["outcome"] | "winners") {
  const selected = rows.filter((row) =>
    group === "winners" ? row.ledger.sourceExitRoiPct > 0 : row.outcome === group
  );
  const window = (row: AuditRow, seconds: number) => row.windows[String(seconds)];
  return {
    n: selected.length,
    measured: selected.filter((row) => row.fetchStatus === "measured").length,
    buys5: median(selected.map((row) => window(row, 5)?.buys ?? NaN)),
    wallets5: median(selected.map((row) => window(row, 5)?.uniqueWallets ?? NaN)),
    buyVolume5: median(selected.map((row) => window(row, 5)?.buyVolumeSol ?? NaN)),
    sells5: median(selected.map((row) => window(row, 5)?.sells ?? NaN)),
    netBuy5: median(selected.map((row) => window(row, 5)?.netBuySol ?? NaN)),
    momentum5: median(selected.map((row) => window(row, 5)?.marketCapMomentumPct ?? NaN)),
    buys60: median(selected.map((row) => window(row, 60)?.buys ?? NaN)),
    wallets60: median(selected.map((row) => window(row, 60)?.uniqueWallets ?? NaN)),
    buyVolume60: median(selected.map((row) => window(row, 60)?.buyVolumeSol ?? NaN)),
    sells60: median(selected.map((row) => window(row, 60)?.sells ?? NaN)),
    leadSeconds: median(selected.map((row) => row.firstPreEntryLeadSeconds ?? NaN)),
  };
}

async function runAudit(
  ledgerPath: string,
  edgeRoot: string,
  sampleSize: number,
  maxMintTxs: number,
  outputTag: string
): Promise<void> {
  const ledger = await readJson<LedgerReport>(ledgerPath);
  if (!ledger?.rows?.length) throw new Error("Ledger report has no campaign rows");
  let edgeEnv: Record<string, string> = {};
  try {
    edgeEnv = parseEnv(await readFile(path.join(edgeRoot, ".env"), "utf8"));
  } catch {}
  const profiles = loadProfiles(edgeEnv);
  const precursors = loadPrecursors(edgeRoot);
  const sample = stratifiedSample(ledger.rows, sampleSize);
  const rows: AuditRow[] = [];

  for (let index = 0; index < sample.length; index++) {
    const ledgerRow = sample[index]!;
    try {
      const history = await fetchMintHistory(ledgerRow.mint, ledgerRow.entryTs, maxMintTxs);
      const fetchedTimestamps = history.txs
        .map((tx) => tx.timestamp)
        .filter((timestamp): timestamp is number => timestamp != null);
      const oldestFetchedTs = fetchedTimestamps.length
        ? Math.min(...fetchedTimestamps)
        : null;
      if (!history.reachedWindow) {
        rows.push({
          ledger: ledgerRow,
          outcome: outcome(ledgerRow),
          fetchStatus: "history_truncated",
          fetchedTransactions: history.txs.length,
          oldestFetchedTs,
          events: 0,
          firstPreEntryLeadSeconds: null,
          windows: {},
          candidates: [],
        });
        console.log(`[${index + 1}/${sample.length}] ${ledgerRow.mint.slice(0, 8)} truncated txs=${history.txs.length}`);
        continue;
      }
      const events = history.txs
        .map((tx) => extractFlowEvent(tx, ledgerRow.mint, ledgerRow.entrySolUsd))
        .filter((event): event is FlowEvent => Boolean(event))
        .filter(
          (event) =>
            event.timestamp >= ledgerRow.entryTs - LOOKBACK_SECONDS &&
            event.timestamp < ledgerRow.entryTs &&
            event.signature !== ledgerRow.entrySignature
        )
        .sort((a, b) => a.timestamp - b.timestamp);
      const windows = Object.fromEntries(
        WINDOWS.map((seconds) => [
          String(seconds),
          windowFeatures(events, ledgerRow.entryTs, seconds),
        ])
      );
      const candidates = events
        .filter((event) => event.side === "buy")
        .map((event) => {
          const causalEvents = events.filter(
            (other) =>
              other.timestamp < event.timestamp ||
              other.signature === event.signature
          );
          const context = windowFeatures(
            causalEvents,
            event.timestamp + 0.001,
            60
          );
          const knownPrecursor = precursors.has(event.wallet);
          const profileScores = Object.fromEntries(
            profiles.map((profile) => [
              profile.name,
              scoreEvent(
                event,
                context,
                knownPrecursor,
                ledgerRow.route === "pump",
                profile.maxBuys,
                profile.maxSells,
                profile.maxBuyVolume
              ),
            ])
          );
          const score = profileScores["current-edge-env"] ?? 0;
          return {
            event,
            score,
            profileScores,
            knownPrecursor,
            context,
            profileGates: Object.fromEntries(
              profiles.map((profile) => [
                profile.name,
                profileGates(
                  event,
                  profileScores[profile.name] ?? 0,
                  context,
                  profile,
                  ledgerRow.route === "pump"
                ),
              ])
            ),
          };
        });
      rows.push({
        ledger: ledgerRow,
        outcome: outcome(ledgerRow),
        fetchStatus: events.length ? "measured" : "no_pre_entry_flow",
        fetchedTransactions: history.txs.length,
        oldestFetchedTs,
        events: events.length,
        firstPreEntryLeadSeconds: events.length
          ? ledgerRow.entryTs - events[0]!.timestamp
          : null,
        windows,
        candidates,
      });
      console.log(
        `[${index + 1}/${sample.length}] ${ledgerRow.mint.slice(0, 8)} ` +
          `outcome=${outcome(ledgerRow)} events=${events.length} candidates=${candidates.length}`
      );
    } catch (error) {
      rows.push({
        ledger: ledgerRow,
        outcome: outcome(ledgerRow),
        fetchStatus: "fetch_error",
        fetchedTransactions: 0,
        oldestFetchedTs: null,
        events: 0,
        firstPreEntryLeadSeconds: null,
        windows: {},
        candidates: [],
        error: error instanceof Error ? error.message : String(error),
      });
      console.warn(
        `[${index + 1}/${sample.length}] ${ledgerRow.mint.slice(0, 8)} error=${rows.at(-1)?.error}`
      );
    }
    await sleep(MINT_SLEEP_MS);
  }

  const profileResults = profiles.map((profile) => {
    const evaluated = rows.map((row) => {
      const passing = row.candidates.find(
        (candidate) => candidate.profileGates[profile.name]?.length === 0
      );
      const closest = [...row.candidates].sort(
        (a, b) =>
          (a.profileGates[profile.name]?.length ?? Infinity) -
            (b.profileGates[profile.name]?.length ?? Infinity) ||
          (b.profileScores[profile.name] ?? 0) -
            (a.profileScores[profile.name] ?? 0)
      )[0];
      return { row, passing, closest };
    });
    const passedRows = evaluated.filter((item) => item.passing).map((item) => item.row.ledger);
    const measurable = evaluated.filter((item) => item.closest);
    const winners = measurable.filter((item) => item.row.ledger.sourceExitRoiPct > 0);
    const losers = measurable.filter((item) => item.row.ledger.sourceExitRoiPct < 0);
    const gateCounts = new Map<string, number>();
    for (const item of winners.filter((row) => !row.passing)) {
      for (const gate of item.closest?.profileGates[profile.name] ?? ["no_candidate"]) {
        gateCounts.set(gate, (gateCounts.get(gate) ?? 0) + 1);
      }
    }
    const rois = passedRows.map((row) => row.sourceExitRoiPct);
    return {
      profile,
      measurable: measurable.length,
      passed: passedRows.length,
      winRatePct: percentage(
        passedRows.filter((row) => row.sourceExitRoiPct > 0).length,
        passedRows.length
      ),
      medianRoiPct: median(rois),
      profitFactor: profitFactor(rois),
      equalBetPnlSol: rois.reduce((sum, roi) => sum + 0.5 * (roi / 100), 0),
      winnerCapturePct: percentage(
        passedRows.filter((row) => row.sourceExitRoiPct > 0).length,
        winners.length
      ),
      scalpCapturePct: percentage(
        passedRows.filter((row) => row.sourceExitRoiPct >= 10).length,
        measurable.filter((row) => row.row.ledger.sourceExitRoiPct >= 10).length
      ),
      runnerCapturePct: percentage(
        passedRows.filter((row) => row.sourceExitRoiPct >= 50).length,
        measurable.filter((row) => row.row.ledger.sourceExitRoiPct >= 50).length
      ),
      loserRejectionPct: percentage(
        losers.filter((row) => !row.passing).length,
        losers.length
      ),
      missedWinnerGates: [...gateCounts.entries()]
        .sort((a, b) => b[1] - a[1])
        .map(([gate, count]) => ({ gate, count })),
    };
  });

  const featureGroups = Object.fromEntries(
    (["winners", "loss", "small_win", "scalp_win", "runner"] as const).map(
      (group) => [group, groupFeatures(rows, group)]
    )
  );
  const output = {
    generatedAt: new Date().toISOString(),
    wallet: ledger.wallet,
    ledgerPath,
    edgeRoot,
    methodology: {
      requestedSample: sampleSize,
      actualSample: sample.length,
      stratifiedBy: ["loss", "small_win", "scalp_win", "runner"],
      lookbackSeconds: LOOKBACK_SECONDS,
      maxMintTransactions: maxMintTxs,
      note:
        "Historical pre-entry flow is reconstructed from Helius transactions that mention each mint. Same-second target-wallet activity is excluded. This is a stratified diagnostic sample, not population PnL.",
    },
    coverage: {
      rows: rows.length,
      measured: rows.filter((row) => row.fetchStatus === "measured").length,
      noPreEntryFlow: rows.filter((row) => row.fetchStatus === "no_pre_entry_flow").length,
      historyTruncated: rows.filter((row) => row.fetchStatus === "history_truncated").length,
      fetchErrors: rows.filter((row) => row.fetchStatus === "fetch_error").length,
      knownPrecursorsLoaded: precursors.size,
    },
    fullLedger: {
      campaigns: ledger.rows.length,
      wins: ledger.rows.filter((row) => row.sourceExitRoiPct > 0).length,
      losses: ledger.rows.filter((row) => row.sourceExitRoiPct < 0).length,
      medianRoiPct: median(ledger.rows.map((row) => row.sourceExitRoiPct)),
      profitFactor: profitFactor(ledger.rows.map((row) => row.sourceExitRoiPct)),
    },
    featureGroups,
    profiles: profileResults,
    rows,
  };

  const dataDir = path.resolve(process.cwd(), "data");
  await mkdir(dataDir, { recursive: true });
  const jsonPath = path.join(dataDir, `${outputTag}.json`);
  const mdPath = path.join(dataDir, `${outputTag}.md`);
  await writeFile(jsonPath, JSON.stringify(output, null, 2), "utf8");
  const groupRows = featureGroups as Record<
    string,
    ReturnType<typeof groupFeatures>
  >;
  const markdown = [
    "# Historical Wallet Pre-Entry Flow Audit",
    "",
    `Generated: ${output.generatedAt}`,
    `Wallet: \`${ledger.wallet}\``,
    "",
    "## Coverage",
    "",
    `- Full ledger campaigns: ${output.fullLedger.campaigns}`,
    `- Stratified campaigns requested: ${sampleSize}`,
    `- Campaigns audited: ${output.coverage.rows}`,
    `- Measured pre-entry flow: ${output.coverage.measured}`,
    `- No pre-entry flow decoded: ${output.coverage.noPreEntryFlow}`,
    `- History truncated before entry window: ${output.coverage.historyTruncated}`,
    `- Fetch errors: ${output.coverage.fetchErrors}`,
    `- Known precursor wallets loaded: ${output.coverage.knownPrecursorsLoaded}`,
    "",
    "## Pre-Entry Indicators",
    "",
    "| Outcome | n | measured | 5s buys | 5s wallets | 5s buy vol | 5s sells | 5s net | 5s MC move | 60s buys | 60s wallets | 60s buy vol | 60s sells | lead |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...["winners", "loss", "small_win", "scalp_win", "runner"].map((name) => {
      const row = groupRows[name]!;
      return `| ${name} | ${row.n} | ${row.measured} | ${row.buys5?.toFixed(1) ?? "-"} | ${row.wallets5?.toFixed(1) ?? "-"} | ${row.buyVolume5?.toFixed(2) ?? "-"} | ${row.sells5?.toFixed(1) ?? "-"} | ${row.netBuy5?.toFixed(2) ?? "-"} | ${fmtPct(row.momentum5)} | ${row.buys60?.toFixed(1) ?? "-"} | ${row.wallets60?.toFixed(1) ?? "-"} | ${row.buyVolume60?.toFixed(2) ?? "-"} | ${row.sells60?.toFixed(1) ?? "-"} | ${row.leadSeconds?.toFixed(1) ?? "-"}s |`;
    }),
    "",
    "## EdgeSniffer Profile Replay",
    "",
    "| Profile | measurable | passed | win | median ROI | PF | equal-bet PnL | winner capture | >=10% capture | runner capture | loser rejection |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ...profileResults.map(
      (row) =>
        `| ${row.profile.name} | ${row.measurable} | ${row.passed} | ${row.winRatePct.toFixed(1)}% | ${fmtPct(row.medianRoiPct)} | ${row.profitFactor?.toFixed(2) ?? "-"} | ${row.equalBetPnlSol.toFixed(3)} SOL | ${row.winnerCapturePct.toFixed(1)}% | ${row.scalpCapturePct.toFixed(1)}% | ${row.runnerCapturePct.toFixed(1)}% | ${row.loserRejectionPct.toFixed(1)}% |`
    ),
    "",
    "## Missed Winner Gates",
    "",
    ...profileResults.flatMap((row) => [
      `### ${row.profile.name}`,
      "",
      ...(row.missedWinnerGates.length
        ? row.missedWinnerGates.map((item) => `- ${item.gate}: ${item.count}`)
        : ["- None in measurable sample"]),
      "",
    ]),
    "## Interpretation",
    "",
    "- This report reconstructs historical pre-entry on-chain flow without Moralis or Gecko.",
    "- The sample is stratified across outcome classes. Do not treat its win rate as the wallet's population win rate.",
    "- Profile replay is diagnostic and in-sample. A compact forward collector is still required for unbiased validation.",
    "- Mint-address history may be incomplete for routes that do not include the mint account in every swap; coverage is reported explicitly.",
    "",
  ].join("\n");
  await writeFile(mdPath, `${markdown}\n`, "utf8");
  console.log(
    JSON.stringify(
      {
        jsonPath,
        mdPath,
        coverage: output.coverage,
        profileSummary: profileResults.map((row) => ({
          profile: row.profile.name,
          measurable: row.measurable,
          passed: row.passed,
          medianRoiPct: row.medianRoiPct,
          profitFactor: row.profitFactor,
          winnerCapturePct: row.winnerCapturePct,
          loserRejectionPct: row.loserRejectionPct,
        })),
      },
      null,
      2
    )
  );
}

async function main(): Promise<void> {
  const ledgerPath = path.resolve(process.argv[2] ?? "");
  const edgeRoot = path.resolve(process.argv[3] ?? "");
  const sampleSize = Number(process.argv[4] ?? "120");
  const outputTag = process.argv[5] ?? "wallet-historical-pre-entry";
  const maxMintTxs = Number(process.argv[6] ?? String(MAX_MINT_TXS_DEFAULT));
  if (!process.argv[2] || !process.argv[3]) {
    throw new Error(
      "usage: tsx src/walletHistoricalPreEntryAudit.ts LEDGER.json EDGE_ROOT [sampleSize] [outputTag] [maxMintTxs]"
    );
  }
  await runAudit(ledgerPath, edgeRoot, sampleSize, maxMintTxs, outputTag);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  });
}
