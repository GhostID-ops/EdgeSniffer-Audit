export interface ParsedBoostSignal {
  mint: string;
  boostAmount: number;
  totalBoosts: number;
  currentMcUsd?: number;
  firstBoostMcUsd?: number;
  athMcUsd?: number;
  growthPct?: number;
}

const MINT_RX = /CA[:\s]+([1-9A-HJ-NP-Za-km-z]{32,44})/i;
const BOOST_AMOUNT_RX = /Boost\s*Amount[:\s]+(\d+)/i;
const TOTAL_BOOSTS_RX = /Total\s*Boosts[:\s]+(\d+)/i;
const CURRENT_MC_RX = /Current\s*MC[:\s]+\$?([\d,.]+)\s*([KMB]?)/i;
const FIRST_BOOST_MC_RX = /First\s*Boost\s*MC[:\s]+\$?([\d,.]+)\s*([KMB]?)/i;
const ATH_MC_RX = /ATH[^\n:]*[:\s]+\$?([\d,.]+)\s*([KMB]?)/i;
const GROWTH_RX = /Growth[^\n]*?([+-]?[\d.]+)\s*%/i;
const SOLANA_RX = /\bsolana\b/i;

function parseMcUsd(num: string, suffix: string): number | undefined {
  const n = parseFloat(num.replace(/,/g, ""));
  if (!Number.isFinite(n)) return undefined;
  const mul =
    suffix.toUpperCase() === "K"
      ? 1_000
      : suffix.toUpperCase() === "M"
      ? 1_000_000
      : suffix.toUpperCase() === "B"
      ? 1_000_000_000
      : 1;
  return n * mul;
}

export function parseBoostSignal(text: string): ParsedBoostSignal | null {
  if (!text) return null;
  if (!SOLANA_RX.test(text)) return null;

  const mintMatch = MINT_RX.exec(text);
  const boostMatch = BOOST_AMOUNT_RX.exec(text);
  const totalMatch = TOTAL_BOOSTS_RX.exec(text);
  if (!mintMatch || !boostMatch || !totalMatch) return null;

  const boostAmount = parseInt(boostMatch[1], 10);
  const totalBoosts = parseInt(totalMatch[1], 10);
  if (!Number.isFinite(boostAmount) || !Number.isFinite(totalBoosts)) return null;

  const currentMcMatch = CURRENT_MC_RX.exec(text);
  const firstBoostMcMatch = FIRST_BOOST_MC_RX.exec(text);
  const athMcMatch = ATH_MC_RX.exec(text);
  const growthMatch = GROWTH_RX.exec(text);

  const result: ParsedBoostSignal = {
    mint: mintMatch[1],
    boostAmount,
    totalBoosts,
  };
  if (currentMcMatch) {
    const v = parseMcUsd(currentMcMatch[1], currentMcMatch[2]);
    if (v !== undefined) result.currentMcUsd = v;
  }
  if (firstBoostMcMatch) {
    const v = parseMcUsd(firstBoostMcMatch[1], firstBoostMcMatch[2]);
    if (v !== undefined) result.firstBoostMcUsd = v;
  }
  if (athMcMatch) {
    const v = parseMcUsd(athMcMatch[1], athMcMatch[2]);
    if (v !== undefined) result.athMcUsd = v;
  }
  if (growthMatch) {
    const g = parseFloat(growthMatch[1]);
    if (Number.isFinite(g)) result.growthPct = g;
  }

  return result;
}

export interface FilterThresholds {
  minBoostAmount: number;
  minTotalBoosts: number;
  /** When > 0, signals with totalBoosts above this are filtered out. 0 = no upper cap. */
  maxTotalBoosts: number;
}

export function passesFilter(
  signal: ParsedBoostSignal,
  thresholds: FilterThresholds
): boolean {
  if (signal.boostAmount < thresholds.minBoostAmount) return false;
  if (signal.totalBoosts < thresholds.minTotalBoosts) return false;
  if (thresholds.maxTotalBoosts > 0 && signal.totalBoosts > thresholds.maxTotalBoosts) return false;
  return true;
}
