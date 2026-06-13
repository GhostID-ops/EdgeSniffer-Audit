import {
  jitoTipFloorUrl,
  jitoTipOracleEnabled,
  jitoTipOracleMaxLamports,
  jitoTipOracleMinLamports,
  jitoTipOraclePercentile,
  jitoTipOracleRefreshMs,
  tipLamports,
} from "./config.js";

type TipFloorRow = Record<string, unknown>;

let cachedTipLamports = Math.max(tipLamports, jitoTipOracleMinLamports);
let lastFetchedAt = 0;
let pollerStarted = false;
let pollerHandle: NodeJS.Timeout | null = null;
let lastRefreshWarningAt = 0;
let suppressedRefreshWarnings = 0;

const WARNING_GAP_MS = 60_000;

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function normalizeTipToLamports(value: number): number {
  // Jito's public tip-floor endpoint has historically returned SOL-denominated decimals.
  // If a provider ever returns lamports instead, large values pass through unchanged.
  return value < 1_000 ? Math.round(value * 1_000_000_000) : Math.round(value);
}

function candidateKeys(): string[] {
  const p = jitoTipOraclePercentile.replace(/^landed_tips_/, "").replace(/_percentile$/, "");
  const normalized = p.startsWith("p") ? p.slice(1) : p;
  return [
    `landed_tips_${normalized}th_percentile`,
    `ema_landed_tips_${normalized}th_percentile`,
    jitoTipOraclePercentile,
    "landed_tips_75th_percentile",
    "ema_landed_tips_75th_percentile",
    "landed_tips_50th_percentile",
    "ema_landed_tips_50th_percentile",
  ];
}

function extractTipLamports(rows: TipFloorRow[]): number | null {
  const row = rows[rows.length - 1];
  if (!row) return null;
  for (const key of candidateKeys()) {
    const value = asNumber(row[key]);
    if (value == null || value <= 0) continue;
    return normalizeTipToLamports(value);
  }
  return null;
}

function clampTip(value: number): number {
  const floored = Math.max(value, tipLamports, jitoTipOracleMinLamports);
  return jitoTipOracleMaxLamports != null ? Math.min(floored, jitoTipOracleMaxLamports) : floored;
}

async function refreshTip(): Promise<void> {
  try {
    const response = await fetch(jitoTipFloorUrl, {
      signal: AbortSignal.timeout(Math.min(1500, Math.max(250, jitoTipOracleRefreshMs - 100))),
    });
    if (!response.ok) {
      throw new Error(`Jito tip floor ${response.status}: ${(await response.text()).slice(0, 120)}`);
    }
    const json = (await response.json()) as unknown;
    const rows = Array.isArray(json) ? (json as TipFloorRow[]) : [json as TipFloorRow];
    const next = extractTipLamports(rows);
    if (next == null) return;
    cachedTipLamports = clampTip(next);
    lastFetchedAt = Date.now();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const now = Date.now();
    suppressedRefreshWarnings += 1;
    if (now - lastRefreshWarningAt >= WARNING_GAP_MS) {
      const suffix =
        suppressedRefreshWarnings > 1 ? ` (${suppressedRefreshWarnings - 1} similar warning(s) suppressed)` : "";
      console.warn(`Jito tip oracle refresh failed (using cached ${cachedTipLamports} lamports): ${msg}${suffix}`);
      lastRefreshWarningAt = now;
      suppressedRefreshWarnings = 0;
    }
  }
}

export function startJitoTipOracle(): void {
  if (pollerStarted || !jitoTipOracleEnabled) return;
  pollerStarted = true;
  void refreshTip();
  pollerHandle = setInterval(() => {
    void refreshTip();
  }, jitoTipOracleRefreshMs);
}

export function stopJitoTipOracle(): void {
  if (pollerHandle) clearInterval(pollerHandle);
  pollerHandle = null;
  pollerStarted = false;
}

export function getDynamicJitoTipLamports(): number {
  if (!jitoTipOracleEnabled) return tipLamports;
  return Math.max(cachedTipLamports, tipLamports, jitoTipOracleMinLamports);
}

export function getJitoTipOracleSnapshot(): {
  enabled: boolean;
  cachedTipLamports: number;
  staticTipLamports: number;
  minLamports: number;
  maxLamports: number | null;
  percentile: string;
  lastFetchedAt: number;
} {
  return {
    enabled: jitoTipOracleEnabled,
    cachedTipLamports,
    staticTipLamports: tipLamports,
    minLamports: jitoTipOracleMinLamports,
    maxLamports: jitoTipOracleMaxLamports,
    percentile: jitoTipOraclePercentile,
    lastFetchedAt,
  };
}
