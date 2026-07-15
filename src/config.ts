import "dotenv/config";
import { Connection, LAMPORTS_PER_SOL } from "@solana/web3.js";
import { dataPath } from "./runtimePaths.js";

const HELIUS_API_KEY = process.env.HELIUS_API_KEY ?? "";
const SENDER_ENDPOINT =
  process.env.SENDER_ENDPOINT ?? "http://ewr-sender.helius-rpc.com/fast";
const SECURE_RPC_URL = process.env.SECURE_RPC_URL ?? "";
let TIP_LAMPORTS = parseInt(
  process.env.TIP_LAMPORTS ?? "65000",
  10
) as number;
let PRIORITY_FEE_MICRO_LAMPORTS = parseInt(
  process.env.PRIORITY_FEE_MICRO_LAMPORTS ?? "50000",
  10
) as number;
let MIRROR_SLIPPAGE_BPS = parseInt(
  process.env.MIRROR_SLIPPAGE_BPS ?? process.env.SWAP_SLIPPAGE_BPS ?? "500",
  10
) as number;
const MIRROR_RETRY_SLIPPAGE_BPS = parseInt(
  process.env.MIRROR_RETRY_SLIPPAGE_BPS ?? "1500",
  10
) as number;

function parseOptionalPositiveNumber(value: string | undefined): number | null {
  if (value == null || value.trim() === "") return null;
  const n = parseFloat(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function parseOptionalPositiveInteger(value: string | undefined): number | null {
  if (value == null || value.trim() === "") return null;
  const n = parseInt(value, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function parseNonNegativeNumber(value: string | undefined, fallback: number): number {
  if (value == null || value.trim() === "") return fallback;
  const n = parseFloat(value);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

function parseOptionalNonNegativeNumber(value: string | undefined): number | null {
  if (value == null || value.trim() === "") return null;
  const n = parseFloat(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function parsePositiveNumber(value: string | undefined, fallback: number): number {
  if (value == null || value.trim() === "") return fallback;
  const n = parseFloat(value);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function parseNonNegativeInteger(value: string | undefined, fallback: number): number {
  if (value == null || value.trim() === "") return fallback;
  const n = parseInt(value, 10);
  return Number.isFinite(n) && n >= 0 ? n : fallback;
}

function parseCsvList(value: string | undefined): string[] {
  return (value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseEnvBool(value: string | undefined, fallback: boolean): boolean {
  const normalized = (value ?? "").trim().toLowerCase();
  if (normalized === "") return fallback;
  if (normalized === "true" || normalized === "1" || normalized === "yes" || normalized === "on") return true;
  if (normalized === "false" || normalized === "0" || normalized === "no" || normalized === "off") return false;
  return fallback;
}

/** Tracked wallet addresses (comma-separated in TRACKED_WALLETS). */
const TRACKED_WALLETS_RAW = process.env.TRACKED_WALLETS ?? "";
export const trackedWallets: string[] = parseCsvList(TRACKED_WALLETS_RAW);

/**
 * Optional exit-only wallets. When enabled, these wallets are watched for sells only; their buys
 * are ignored. A sell signal closes our matching open position for that mint.
 */
export const exitSignalSellsEnabled = parseEnvBool(process.env.EXIT_SIGNAL_SELLS_ENABLED, false);
export const exitSignalWallets: string[] = parseCsvList(process.env.EXIT_SIGNAL_WALLETS);

/** Min buy size in SOL. Behavior for smaller copy-sized buys is controlled by MIN_BUY_BEHAVIOR. */
let MIN_BUY_SOL = parsePositiveNumber(process.env.MIN_BUY_SOL, 0.01);
/** Max buy size in SOL. Mirror trades are capped to this. Default 1 SOL. */
let MAX_BUY_SOL = parsePositiveNumber(process.env.MAX_BUY_SOL, 1);
export type MinBuyBehavior = "skip" | "floor" | "allow";
function parseMinBuyBehavior(raw: string | undefined): MinBuyBehavior {
  const value = (raw ?? "skip").trim().toLowerCase();
  if (value === "allow" || value === "copy" || value === "copy-sized") return "allow";
  if (value === "floor" || value === "min") return "floor";
  return "skip";
}
/**
 * How to handle percentage-sized copy buys below MIN_BUY_SOL/MIN_BUY_USDC:
 * - skip: do not buy
 * - floor: raise the buy to the configured minimum
 * - allow: buy the calculated copy-sized amount
 */
export const minBuyBehavior = parseMinBuyBehavior(process.env.MIN_BUY_BEHAVIOR);
const COPY_BUY_PERCENTAGE_RAW = process.env.COPY_BUY_PERCENTAGE ?? "100";
const COPY_BUY_PERCENTAGE_PARSED = parseFloat(COPY_BUY_PERCENTAGE_RAW);
if (!Number.isFinite(COPY_BUY_PERCENTAGE_PARSED)) {
  throw new Error(`COPY_BUY_PERCENTAGE must be a number between 0 and 100, got ${COPY_BUY_PERCENTAGE_RAW}`);
}
if (COPY_BUY_PERCENTAGE_PARSED < 0) {
  throw new Error(`COPY_BUY_PERCENTAGE cannot be negative, got ${COPY_BUY_PERCENTAGE_RAW}`);
}
/** Percentage of source wallet buy size to copy. Values above 100 are clamped to 100. */
let COPY_BUY_PERCENTAGE = Math.min(100, COPY_BUY_PERCENTAGE_PARSED);
export let copyBuyPercentage = COPY_BUY_PERCENTAGE;
const MIN_SOURCE_BUY_SOL = parseOptionalNonNegativeNumber(process.env.MIN_SOURCE_BUY_SOL);
/**
 * Optional source-spend gate for SOL copy buys. This is especially important in
 * fixed-size mode: a detector can surface a launch/create/failed source event
 * with a zero SOL spend, and fixed-size copying would otherwise buy anyway.
 */
export const minSourceBuyLamports =
  MIN_SOURCE_BUY_SOL != null ? Math.round(MIN_SOURCE_BUY_SOL * LAMPORTS_PER_SOL) : null;
const MIN_BUY_USDC = parsePositiveNumber(process.env.MIN_BUY_USDC, 50);
const MAX_BUY_USDC = parsePositiveNumber(process.env.MAX_BUY_USDC, 60);
/** SOL reserve amount to keep for gas fees. Prevents wallet from going to 0 SOL. Default 0.1 SOL. */
let SOL_RESERVE = parseFloat(process.env.SOL_RESERVE ?? "0.1");
const MIRROR_QUOTE_ASSET_RAW = (process.env.MIRROR_QUOTE_ASSET ?? "SOL").toUpperCase();
export type MirrorQuoteAsset = "SOL" | "USDC" | "MATCH";
export const mirrorQuoteAsset: MirrorQuoteAsset =
  MIRROR_QUOTE_ASSET_RAW === "USDC" || MIRROR_QUOTE_ASSET_RAW === "MATCH"
    ? MIRROR_QUOTE_ASSET_RAW
    : "SOL";

/**
 * When false, the bot ignores source buys paid in USDC (i.e. USDC-quoted pools).
 * These tokens often have no pump.fun/PumpSwap sell route, so with Jupiter fallback
 * disabled they can be bought but never sold — leaving untradeable bags. Default false
 * keeps behavior "normal" (SOL-quoted snipes only). Set MIRROR_USDC_QUOTE_BUYS=true to opt in.
 */
export const mirrorUsdcQuoteBuysEnabled = parseEnvBool(
  process.env.MIRROR_USDC_QUOTE_BUYS,
  false
);

/**
 * Soft cap on entry price impact (percent). When a buy quote exceeds this, the bot reduces size
 * and re-quotes (rather than skipping the trade outright). If the reduced quote still exceeds
 * the cap and the trade can't fit at MIN_BUY_SOL, the trade is skipped. Empty/0 = disabled.
 */
export const maxEntryPriceImpactPct = parseOptionalPositiveNumber(
  process.env.MAX_ENTRY_PRICE_IMPACT_PCT
);
/** Number of size-reduction attempts before giving up on the entry. Default 2. */
export const maxImpactReduceAttempts = Math.max(
  0,
  parseInt(process.env.MAX_IMPACT_REDUCE_ATTEMPTS ?? "2", 10)
);

/**
 * Maximum drift between source wallet's effective fill price and our quoted price (percent).
 * If our price is more than this % above the source's, we skip — we're chasing, not copying.
 * Empty/0 = disabled. Buys only.
 */
export const maxSourceDriftPct = parseOptionalPositiveNumber(process.env.MAX_SOURCE_DRIFT_PCT);

/**
 * Daily session loss limit in lamports. When cumulative realized PnL since session start drops
 * below this threshold, new buys are blocked for the rest of the session. Existing positions can
 * still exit. Empty/0 = disabled.
 */
const DAILY_LOSS_LIMIT_SOL = parseOptionalPositiveNumber(process.env.DAILY_LOSS_LIMIT_SOL);
export const dailyLossLimitLamports =
  DAILY_LOSS_LIMIT_SOL != null ? Math.round(DAILY_LOSS_LIMIT_SOL * LAMPORTS_PER_SOL) : null;

/** When N losing trades fire in a row, pause new buys. 0 = disabled. */
export const consecutiveLossLimit = Math.max(
  0,
  parseInt(process.env.CONSECUTIVE_LOSS_LIMIT ?? "0", 10)
);
/** Pause duration in ms after a circuit breaker trip. */
export const circuitBreakerPauseMs = Math.max(
  0,
  parseInt(process.env.CIRCUIT_BREAKER_PAUSE_MS ?? "600000", 10)
);

/**
 * Stair-step trailing stop: when a position's peak PnL clears `peakPct`, the effective stop
 * threshold is raised to `floorPct` (signed — can be 0, 5, -5, etc).
 * Format: "peakPct:floorPct,peakPct:floorPct,..." — empty string disables.
 * Example: "10:0,20:5" = "at +10% peak, lock breakeven; at +20% peak, lock +5%".
 *
 * Steps are sorted ascending by peakPct internally. Effective stop = max(steps that have triggered),
 * starting from the base -STOP_LOSS_PCT.
 */
export type TrailingStep = { peakPct: number; floorPct: number };
export type ProfitLadderStep = { triggerPct: number; sellPct: number };
function parseTrailingSteps(raw: string | undefined): TrailingStep[] {
  if (!raw || raw.trim() === "") return [];
  const out: TrailingStep[] = [];
  for (const piece of raw.split(",")) {
    const [pkRaw, flRaw] = piece.split(":");
    if (pkRaw == null || flRaw == null) continue;
    const peakPct = parseFloat(pkRaw);
    const floorPct = parseFloat(flRaw);
    if (!Number.isFinite(peakPct) || !Number.isFinite(floorPct)) continue;
    out.push({ peakPct, floorPct });
  }
  out.sort((a, b) => a.peakPct - b.peakPct);
  return out;
}
function parseProfitLadderSteps(raw: string | undefined): ProfitLadderStep[] {
  if (!raw || raw.trim() === "") return [];
  const out: ProfitLadderStep[] = [];
  for (const piece of raw.split(",")) {
    const [triggerRaw, sellRaw] = piece.split(":");
    if (triggerRaw == null || sellRaw == null) continue;
    const triggerPct = parseFloat(triggerRaw);
    const sellPct = parseFloat(sellRaw);
    if (!Number.isFinite(triggerPct) || !Number.isFinite(sellPct)) continue;
    if (triggerPct <= 0 || sellPct <= 0) continue;
    out.push({ triggerPct, sellPct: Math.min(100, sellPct) });
  }
  out.sort((a, b) => a.triggerPct - b.triggerPct);
  return out;
}
export const trailingSteps = parseTrailingSteps(process.env.TRAILING_STEPS);
export const trailingEnabled = trailingSteps.length > 0;
export const profitLadderSteps = parseProfitLadderSteps(process.env.PROFIT_LADDER_STEPS);
export const profitLadderEnabled = parseEnvBool(
  process.env.PROFIT_LADDER_ENABLED,
  profitLadderSteps.length > 0
) && profitLadderSteps.length > 0;

/**
 * Cluster trigger strategy. When enabled, buys from CLUSTER_WALLETS (the front-runner wallets,
 * e.g. 5qn / EAz) only mirror when that single source buy is at least CLUSTER_MIN_SOURCE_BUY_SOL.
 * The entry stays a solo, immediate mirror (no waiting for a partner) so it lands same/next slot.
 *
 * CLUSTER_CONFIRM_WALLET (e.g. GUg) is watched but never triggers an entry. If it buys the same
 * mint within CLUSTER_CONFIRM_WINDOW_MS after our entry, the position keeps normal trailing. If it
 * does not confirm in that window, the position switches to the tighter CLUSTER_NO_CONFIRM_TRAILING_STEPS.
 */
export const clusterTriggerEnabled = parseEnvBool(process.env.CLUSTER_TRIGGER_ENABLED, false);
/** Front-runner wallets the size gate applies to. Must also be present in TRACKED_WALLETS to be watched. */
export const clusterWallets: string[] = parseCsvList(process.env.CLUSTER_WALLETS);
/** Confirmation-only wallet (watched, never triggers a buy). */
export const clusterConfirmWallet: string = (process.env.CLUSTER_CONFIRM_WALLET ?? "").trim();
const CLUSTER_MIN_SOURCE_BUY_SOL = parseFloat(process.env.CLUSTER_MIN_SOURCE_BUY_SOL ?? "8");
/** Minimum SOL the source must spend on a single buy for a cluster wallet to be mirrored. */
export const clusterMinSourceBuyLamports =
  Number.isFinite(CLUSTER_MIN_SOURCE_BUY_SOL) && CLUSTER_MIN_SOURCE_BUY_SOL > 0
    ? Math.round(CLUSTER_MIN_SOURCE_BUY_SOL * LAMPORTS_PER_SOL)
    : 0;
/** How long after entry to wait for the confirmation wallet before tightening the trail. */
export const clusterConfirmWindowMs = parseNonNegativeInteger(process.env.CLUSTER_CONFIRM_WINDOW_MS, 25000);
/** Tighter trailing steps used when the confirmation wallet does not appear in time. */
export const clusterNoConfirmTrailingSteps = parseTrailingSteps(process.env.CLUSTER_NO_CONFIRM_TRAILING_STEPS);

/**
 * When true (default), buys whose output mint is a pre-graduation pump.fun bonding-curve token
 * are routed directly to the pump.fun program — bypassing Jupiter entirely. Falls back to Jupiter
 * for graduated tokens or when curve detection fails.
 */
const PUMPFUN_DIRECT_RAW = (process.env.PUMPFUN_DIRECT_ENABLED ?? "true").toLowerCase();
export const pumpfunDirectEnabled =
  PUMPFUN_DIRECT_RAW !== "false" && PUMPFUN_DIRECT_RAW !== "0" && PUMPFUN_DIRECT_RAW !== "no";
const PUMPSWAP_DIRECT_BUY_RAW = (process.env.PUMPSWAP_DIRECT_BUY_ENABLED ?? "true").toLowerCase();
export const pumpSwapDirectBuyEnabled =
  PUMPSWAP_DIRECT_BUY_RAW !== "false" &&
  PUMPSWAP_DIRECT_BUY_RAW !== "0" &&
  PUMPSWAP_DIRECT_BUY_RAW !== "no";
export const pumpSwapDirectMinOutBps = Math.max(
  0,
  Math.min(10_000, parseInt(process.env.PUMPSWAP_DIRECT_MIN_OUT_BPS ?? "1", 10))
);
const PUMPSWAP_DIRECT_PREFLIGHT_RAW = (process.env.PUMPSWAP_DIRECT_PREFLIGHT ?? "false").toLowerCase();
export const pumpSwapDirectPreflight =
  PUMPSWAP_DIRECT_PREFLIGHT_RAW !== "false" &&
  PUMPSWAP_DIRECT_PREFLIGHT_RAW !== "0" &&
  PUMPSWAP_DIRECT_PREFLIGHT_RAW !== "no";
const PUMPSWAP_RUST_PREFLIGHT_RAW = (process.env.PUMPSWAP_RUST_PREFLIGHT ?? "false").toLowerCase();
export const pumpSwapRustPreflight =
  PUMPSWAP_RUST_PREFLIGHT_RAW === "true" ||
  PUMPSWAP_RUST_PREFLIGHT_RAW === "1" ||
  PUMPSWAP_RUST_PREFLIGHT_RAW === "yes";
const JUPITER_FALLBACK_ENABLED_RAW = (process.env.JUPITER_FALLBACK_ENABLED ?? "true").toLowerCase();
export const jupiterFallbackEnabled =
  JUPITER_FALLBACK_ENABLED_RAW !== "false" &&
  JUPITER_FALLBACK_ENABLED_RAW !== "0" &&
  JUPITER_FALLBACK_ENABLED_RAW !== "no";
export const bagsDirectEnabled = parseEnvBool(process.env.BAGS_DIRECT_ENABLED, false);
export const bagsMintSuffixFilter = (process.env.BAGS_MINT_SUFFIX_FILTER ?? "BAGS").trim();
const MIRROR_GRADUATED_BUYS_RAW = (
  process.env.MIRROR_GRADUATED_BUYS ??
  process.env.BUY_GRADUATED_TOKENS ??
  "false"
).toLowerCase();
export const mirrorGraduatedBuys =
  MIRROR_GRADUATED_BUYS_RAW === "true" ||
  MIRROR_GRADUATED_BUYS_RAW === "1" ||
  MIRROR_GRADUATED_BUYS_RAW === "yes";
const PUMPFUN_EXACT_SOL_IN_BUYS_RAW = (process.env.PUMPFUN_EXACT_SOL_IN_BUYS ?? "true").toLowerCase();
export const pumpfunExactSolInBuys =
  PUMPFUN_EXACT_SOL_IN_BUYS_RAW !== "false" &&
  PUMPFUN_EXACT_SOL_IN_BUYS_RAW !== "0" &&
  PUMPFUN_EXACT_SOL_IN_BUYS_RAW !== "no";

/**
 * Dynamic priority fees: background poll of getRecentPrioritizationFees on pump.fun program;
 * effective fee = max(static_floor, p75 of recent fees). Default ON.
 * The static PRIORITY_FEE_MICRO_LAMPORTS is the floor — we never go below it.
 */
const DYN_FEE_RAW = (process.env.DYNAMIC_PRIORITY_FEE_ENABLED ?? "true").toLowerCase();
export const dynamicPriorityFeeEnabled =
  DYN_FEE_RAW !== "false" && DYN_FEE_RAW !== "0" && DYN_FEE_RAW !== "no";
export const dynamicPriorityFeeRefreshMs = Math.max(
  1000,
  parseInt(process.env.DYNAMIC_PRIORITY_FEE_REFRESH_MS ?? "5000", 10)
);
export const dynamicPriorityFeeMaxMicroLamports = parseOptionalPositiveInteger(
  process.env.DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS
);
let MAX_BUY_EXECUTION_FEE_SOL = parseOptionalPositiveNumber(process.env.MAX_BUY_EXECUTION_FEE_SOL);
let MAX_BUY_EXECUTION_FEE_PCT_OF_BUY = parseOptionalNonNegativeNumber(
  process.env.MAX_BUY_EXECUTION_FEE_PCT_OF_BUY
);
export let maxBuyExecutionFeeLamports =
  MAX_BUY_EXECUTION_FEE_SOL == null
    ? null
    : Math.max(0, Math.round(MAX_BUY_EXECUTION_FEE_SOL * LAMPORTS_PER_SOL));
export let maxBuyExecutionFeePctOfBuy: number | null =
  MAX_BUY_EXECUTION_FEE_PCT_OF_BUY != null &&
  Number.isFinite(MAX_BUY_EXECUTION_FEE_PCT_OF_BUY) &&
  MAX_BUY_EXECUTION_FEE_PCT_OF_BUY >= 0
    ? MAX_BUY_EXECUTION_FEE_PCT_OF_BUY
    : null;

/** Helius staked RPC URL for read-only operations (blockhash, ALTs). */
export const readRpcUrl =
  SECURE_RPC_URL.length > 0
    ? SECURE_RPC_URL
    : HELIUS_API_KEY.length > 0
    ? `https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}`
    : "https://mainnet.helius-rpc.com";

/** Sender endpoint URL for sendTransaction and confirmation polling. */
export const senderEndpointUrl = SENDER_ENDPOINT;

const EXECUTION_TELEMETRY_ENABLED_RAW = (process.env.EXECUTION_TELEMETRY_ENABLED ?? "true").toLowerCase();
export const executionTelemetryEnabled =
  EXECUTION_TELEMETRY_ENABLED_RAW !== "false" &&
  EXECUTION_TELEMETRY_ENABLED_RAW !== "0" &&
  EXECUTION_TELEMETRY_ENABLED_RAW !== "no";
export const executionTelemetryPath = process.env.EXECUTION_TELEMETRY_PATH ?? dataPath("execution-telemetry.jsonl");
const SENDER_RPC_FALLBACK_ENABLED_RAW = (process.env.SENDER_RPC_FALLBACK_ENABLED ?? "true").toLowerCase();
export const senderRpcFallbackEnabled =
  SENDER_RPC_FALLBACK_ENABLED_RAW !== "false" &&
  SENDER_RPC_FALLBACK_ENABLED_RAW !== "0" &&
  SENDER_RPC_FALLBACK_ENABLED_RAW !== "no";
const EXECUTION_MULTI_SEND_ENABLED_RAW = (process.env.EXECUTION_MULTI_SEND_ENABLED ?? "false").toLowerCase();
export const executionMultiSendEnabled =
  EXECUTION_MULTI_SEND_ENABLED_RAW === "true" ||
  EXECUTION_MULTI_SEND_ENABLED_RAW === "1" ||
  EXECUTION_MULTI_SEND_ENABLED_RAW === "yes";
const JITO_SEND_MODE_RAW = (process.env.JITO_SEND_MODE ?? "bundle").toLowerCase().trim();
export type JitoSendMode = "bundle" | "transaction" | "race";
export const jitoSendMode: JitoSendMode =
  JITO_SEND_MODE_RAW === "transaction" || JITO_SEND_MODE_RAW === "tx"
    ? "transaction"
    : JITO_SEND_MODE_RAW === "race" || JITO_SEND_MODE_RAW === "both"
      ? "race"
      : "bundle";
export const executionRescueRetries = Math.max(
  0,
  parseInt(process.env.EXECUTION_RESCUE_RETRIES ?? "2", 10)
);
export const executionRescueDelayMs = Math.max(
  0,
  parseInt(process.env.EXECUTION_RESCUE_DELAY_MS ?? "25", 10)
);
export const executionAltAddresses = (process.env.EXECUTION_ALT_ADDRESSES ?? "")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);
const DEFAULT_EXECUTION_TIP_MULTIPLIERS: Array<[string, number]> = [
  ["buy", 1.15],
  ["sell", 0.9],
  ["pumpfun-direct-buy", 1.2],
  ["pumpfun-source-replay-buy", 1.25],
  ["pumpswap-direct-buy", 1.15],
  ["jupiter-buy", 1.0],
  ["pumpfun-direct-sell", 0.9],
  ["pumpfun-direct-sell-v2", 1.0],
  ["pumpswap-direct-sell", 0.95],
  ["jupiter-sell", 0.9],
];
const configuredExecutionTipMultipliers = (process.env.EXECUTION_TIP_MULTIPLIERS ?? "")
    .split(",")
    .map((piece) => piece.trim())
    .filter(Boolean)
    .map((piece): [string, number] | null => {
      const [route, value] = piece.split(":");
      const multiplier = parseFloat(value ?? "");
      if (!route || !Number.isFinite(multiplier) || multiplier < 0) return null;
      return [route.trim(), multiplier];
    })
    .filter((entry): entry is [string, number] => entry != null);
export const executionTipMultipliers = new Map([
  ...DEFAULT_EXECUTION_TIP_MULTIPLIERS,
  ...configuredExecutionTipMultipliers,
]);
const DEFAULT_EXECUTION_PRIORITY_FEE_MULTIPLIERS: Array<[string, number]> = [
  ["buy", 1],
  ["sell", 1],
];
const configuredExecutionPriorityFeeMultipliers = (process.env.EXECUTION_PRIORITY_FEE_MULTIPLIERS ?? "")
  .split(",")
  .map((piece) => piece.trim())
  .filter(Boolean)
  .map((piece): [string, number] | null => {
    const [route, value] = piece.split(":");
    const multiplier = parseFloat(value ?? "");
    if (!route || !Number.isFinite(multiplier) || multiplier < 0) return null;
    return [route.trim(), multiplier];
  })
  .filter((entry): entry is [string, number] => entry != null);
export const executionPriorityFeeMultipliers = new Map([
  ...DEFAULT_EXECUTION_PRIORITY_FEE_MULTIPLIERS,
  ...configuredExecutionPriorityFeeMultipliers,
]);
const WARM_WALLET_MODE_RAW = (process.env.WARM_WALLET_MODE ?? "true").toLowerCase();
export const warmWalletMode =
  WARM_WALLET_MODE_RAW !== "false" &&
  WARM_WALLET_MODE_RAW !== "0" &&
  WARM_WALLET_MODE_RAW !== "no";
const KEEP_WSOL_ATA_OPEN_RAW = (process.env.KEEP_WSOL_ATA_OPEN ?? "true").toLowerCase();
export const keepWsolAtaOpen =
  KEEP_WSOL_ATA_OPEN_RAW !== "false" &&
  KEEP_WSOL_ATA_OPEN_RAW !== "0" &&
  KEEP_WSOL_ATA_OPEN_RAW !== "no";

// --- Jito direct bundle submission ---
// When enabled, broadcasts go straight to Jito's BlockEngine /api/v1/bundles endpoint instead
// of Helius Sender (which forwards bundles to Jito with one extra hop). Saves ~50-100ms on the
// hot path when Jito accepts the bundle. Falls back to Helius Sender on Jito error if
// USE_JITO_FALLBACK=true.
const USE_JITO_DIRECT_RAW = (process.env.USE_JITO_DIRECT ?? "false").toLowerCase();
export const useJitoDirect =
  USE_JITO_DIRECT_RAW === "true" || USE_JITO_DIRECT_RAW === "1" || USE_JITO_DIRECT_RAW === "yes";
const USE_JITO_FALLBACK_RAW = (process.env.USE_JITO_FALLBACK ?? "true").toLowerCase();
export const useJitoFallback =
  USE_JITO_FALLBACK_RAW === "true" || USE_JITO_FALLBACK_RAW === "1" || USE_JITO_FALLBACK_RAW === "yes";
const BROADCAST_RACE_ENABLED_RAW = (process.env.BROADCAST_RACE_ENABLED ?? "false").toLowerCase();
export const broadcastRaceEnabled =
  BROADCAST_RACE_ENABLED_RAW === "true" ||
  BROADCAST_RACE_ENABLED_RAW === "1" ||
  BROADCAST_RACE_ENABLED_RAW === "yes";
const JITO_REGION = (process.env.JITO_REGION ?? "").toLowerCase().trim();
const JITO_REGIONS_RAW = process.env.JITO_REGIONS ?? "";
// Jito's regional block engines. Each forwards to the same global validator set; the regional
// hostname just gives you a closer edge ingress. Empty string = use the regionless global.
const JITO_REGIONS: Record<string, string> = {
  "": "https://mainnet.block-engine.jito.wtf",
  ny: "https://ny.mainnet.block-engine.jito.wtf",
  "new-york": "https://ny.mainnet.block-engine.jito.wtf",
  newyork: "https://ny.mainnet.block-engine.jito.wtf",
  ewr: "https://ny.mainnet.block-engine.jito.wtf",
  va: "https://ny.mainnet.block-engine.jito.wtf",
  virginia: "https://ny.mainnet.block-engine.jito.wtf",
  ashburn: "https://ny.mainnet.block-engine.jito.wtf",
  amsterdam: "https://amsterdam.mainnet.block-engine.jito.wtf",
  ams: "https://amsterdam.mainnet.block-engine.jito.wtf",
  dublin: "https://dublin.mainnet.block-engine.jito.wtf",
  frankfurt: "https://frankfurt.mainnet.block-engine.jito.wtf",
  fra: "https://frankfurt.mainnet.block-engine.jito.wtf",
  london: "https://london.mainnet.block-engine.jito.wtf",
  lon: "https://london.mainnet.block-engine.jito.wtf",
  singapore: "https://singapore.mainnet.block-engine.jito.wtf",
  sg: "https://singapore.mainnet.block-engine.jito.wtf",
  sgp: "https://singapore.mainnet.block-engine.jito.wtf",
  tokyo: "https://tokyo.mainnet.block-engine.jito.wtf",
  tyo: "https://tokyo.mainnet.block-engine.jito.wtf",
  "slc": "https://slc.mainnet.block-engine.jito.wtf",
  "salt-lake-city": "https://slc.mainnet.block-engine.jito.wtf",
};
const configuredJitoRegions =
  JITO_REGIONS_RAW.trim().length > 0
    ? JITO_REGIONS_RAW.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)
    : [JITO_REGION];
export const jitoBundleUrls = Array.from(
  new Set(
    configuredJitoRegions.map(
      (region) => `${JITO_REGIONS[region] ?? JITO_REGIONS[""]}/api/v1/bundles`
    )
  )
);
export const jitoBundleUrl = jitoBundleUrls[0] ?? `${JITO_REGIONS[""]}/api/v1/bundles`;
export const jitoTransactionUrls = Array.from(
  new Set(
    configuredJitoRegions.map(
      (region) => `${JITO_REGIONS[region] ?? JITO_REGIONS[""]}/api/v1/transactions`
    )
  )
);
export const jitoTransactionUrl =
  jitoTransactionUrls[0] ?? `${JITO_REGIONS[""]}/api/v1/transactions`;
export const jitoAuthUuid = process.env.JITO_AUTH_UUID ?? "";
export const jitoRegionRaceEnabled = parseEnvBool(process.env.JITO_REGION_RACE_ENABLED, true);
export const jitoTipFloorUrl =
  process.env.JITO_TIP_FLOOR_URL ?? "https://bundles.jito.wtf/api/v1/bundles/tip_floor";
export const jitoTipOracleEnabled = parseEnvBool(process.env.JITO_TIP_ORACLE_ENABLED, false);
export const jitoTipOracleRefreshMs = Math.max(
  1000,
  parseInt(process.env.JITO_TIP_ORACLE_REFRESH_MS ?? "5000", 10)
);
export const jitoTipOraclePercentile = (
  process.env.JITO_TIP_ORACLE_PERCENTILE ?? "p75"
).toLowerCase().trim();
export const jitoTipOracleMinLamports = Math.max(
  0,
  parseNonNegativeInteger(process.env.JITO_TIP_ORACLE_MIN_LAMPORTS, TIP_LAMPORTS)
);
export const jitoTipOracleMaxLamports = parseOptionalPositiveInteger(
  process.env.JITO_TIP_ORACLE_MAX_LAMPORTS
);
export const jitoConnectionWarmupEnabled = parseEnvBool(
  process.env.JITO_CONNECTION_WARMUP_ENABLED,
  false
);
export const jitoConnectionWarmupMs = Math.max(
  5000,
  parseInt(process.env.JITO_CONNECTION_WARMUP_MS ?? "15000", 10)
);

/** Helius enhanced transactions API (parse transactions). POST with { transactions: [sigs] }. */
export const heliusEnhancedTransactionsUrl =
  HELIUS_API_KEY.length > 0
    ? `https://api-mainnet.helius-rpc.com/v0/transactions/?api-key=${HELIUS_API_KEY}`
    : "";

/** Helius Parse Transaction History base. GET {base}/{address}/transactions?api-key=... */
export function heliusAddressTransactionsUrl(
  address: string,
  before?: string,
  limit: number = 5
): string {
  if (!HELIUS_API_KEY) return "";
  const params = new URLSearchParams({
    "api-key": HELIUS_API_KEY,
    limit: String(limit),
  });
  if (before) params.set("before", before);
  return `https://api-mainnet.helius-rpc.com/v0/addresses/${address}/transactions?${params.toString()}`;
}

/** WebSocket endpoint for logsSubscribe push notifications. */
export const websocketUrl = process.env.WEBSOCKET_URL ?? "";

const HELIUS_TX_SUBSCRIBE_ENABLED_RAW = (
  process.env.HELIUS_TX_SUBSCRIBE_ENABLED ?? "false"
).toLowerCase();
export const heliusTxSubscribeEnabled =
  HELIUS_TX_SUBSCRIBE_ENABLED_RAW === "true" ||
  HELIUS_TX_SUBSCRIBE_ENABLED_RAW === "1" ||
  HELIUS_TX_SUBSCRIBE_ENABLED_RAW === "yes";
export const heliusTxSubscribeUrl =
  process.env.HELIUS_TX_SUBSCRIBE_URL ??
  (HELIUS_API_KEY.length > 0
    ? `wss://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}`
    : "");
export const heliusTxSubscribeCommitment =
  (process.env.HELIUS_TX_SUBSCRIBE_COMMITMENT ?? "processed").toLowerCase();
export const heliusTxSubscribeDetails =
  (process.env.HELIUS_TX_SUBSCRIBE_DETAILS ?? "signatures").toLowerCase();

const SHYFT_GRPC_ENABLED_RAW = (process.env.SHYFT_GRPC_ENABLED ?? "false").toLowerCase();
export const shyftGrpcEnabled =
  SHYFT_GRPC_ENABLED_RAW === "true" ||
  SHYFT_GRPC_ENABLED_RAW === "1" ||
  SHYFT_GRPC_ENABLED_RAW === "yes";
export const shyftGrpcUrl = process.env.SHYFT_GRPC_URL ?? "";
export const shyftGrpcXToken = process.env.SHYFT_GRPC_X_TOKEN ?? process.env.SHYFT_GRPC_TOKEN ?? "";
export const shyftGrpcCommitment = (process.env.SHYFT_GRPC_COMMITMENT ?? "processed").toLowerCase();
export const shyftGrpcReconnectMs = Math.max(
  250,
  parseInt(process.env.SHYFT_GRPC_RECONNECT_MS ?? "1000", 10)
);
const SHYFT_RABBITSTREAM_ENABLED_RAW = (process.env.SHYFT_RABBITSTREAM_ENABLED ?? "false").toLowerCase();
export const shyftRabbitStreamEnabled =
  SHYFT_RABBITSTREAM_ENABLED_RAW === "true" ||
  SHYFT_RABBITSTREAM_ENABLED_RAW === "1" ||
  SHYFT_RABBITSTREAM_ENABLED_RAW === "yes";
export const shyftRabbitStreamUrl = process.env.SHYFT_RABBITSTREAM_URL ?? "";

const RUST_SIDECAR_ENABLED_RAW = (process.env.RUST_SIDECAR_ENABLED ?? "false").toLowerCase();
export const rustSidecarEnabled =
  RUST_SIDECAR_ENABLED_RAW === "true" ||
  RUST_SIDECAR_ENABLED_RAW === "1" ||
  RUST_SIDECAR_ENABLED_RAW === "yes";
export const rustSidecarBin =
  process.env.RUST_SIDECAR_BIN ?? "rust-sidecar\\target\\release\\pumpfun-sidecar.exe";
export const rustSidecarUseCargo =
  (process.env.RUST_SIDECAR_USE_CARGO ?? "false").toLowerCase() === "true";
export const rustExecutorEnabled = parseEnvBool(process.env.RUST_EXECUTOR_ENABLED, false);
export const rustExecutorTimeoutMs = Math.max(
  250,
  parseInt(process.env.RUST_EXECUTOR_TIMEOUT_MS ?? "1500", 10)
);
export const rustHotSniperEnabled = parseEnvBool(process.env.RUST_HOT_SNIPER_ENABLED, false);
export const rustOnlyBuys = parseEnvBool(process.env.RUST_ONLY_BUYS, false);
export const rustOnlySells = parseEnvBool(process.env.RUST_ONLY_SELLS, false);
export const rustHotSniperPrimary = parseEnvBool(process.env.RUST_HOT_SNIPER_PRIMARY, false);
export const rustHotSniperPrimaryFallbackEnabled = parseEnvBool(
  process.env.RUST_HOT_SNIPER_PRIMARY_FALLBACK_ENABLED,
  false
);
export const rustHotSniperPrimaryFallbackDelayMs = Math.max(
  0,
  parseInt(process.env.RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS ?? "80", 10)
);
const WATCHER_POLL_FALLBACK_ENABLED_RAW = process.env.WATCHER_POLL_FALLBACK_ENABLED;
const STREAM_DETECTION_ENABLED =
  shyftGrpcEnabled ||
  shyftRabbitStreamEnabled ||
  rustSidecarEnabled ||
  heliusTxSubscribeEnabled ||
  websocketUrl.trim().length > 0;
export const watcherPollFallbackEnabled =
  WATCHER_POLL_FALLBACK_ENABLED_RAW == null
    ? !STREAM_DETECTION_ENABLED
    : parseEnvBool(WATCHER_POLL_FALLBACK_ENABLED_RAW, false);

/** When false, the bot ignores source-wallet sells (you exit manually via 'e' or 'a' keys). Default true. */
const MIRROR_SELLS_RAW = (process.env.MIRROR_SELLS ?? "true").toLowerCase();
export let mirrorSells =
  MIRROR_SELLS_RAW !== "false" && MIRROR_SELLS_RAW !== "0" && MIRROR_SELLS_RAW !== "no";
export type MirrorSellMode = "full" | "proportional";
const MIRROR_SELL_MODE_RAW = (process.env.MIRROR_SELL_MODE ?? "full").trim().toLowerCase();
export const mirrorSellMode: MirrorSellMode =
  MIRROR_SELL_MODE_RAW === "proportional" || MIRROR_SELL_MODE_RAW === "chunk" ? "proportional" : "full";
export const mirrorSellProportionalMinPct = Math.max(
  0,
  Math.min(100, parseFloat(process.env.MIRROR_SELL_PROPORTIONAL_MIN_PCT ?? "0"))
);
export const mirrorSellProportionalMaxPct = Math.max(
  mirrorSellProportionalMinPct,
  Math.min(100, parseFloat(process.env.MIRROR_SELL_PROPORTIONAL_MAX_PCT ?? "100"))
);
export const mirrorSellProportionalFallback =
  (process.env.MIRROR_SELL_PROPORTIONAL_FALLBACK ?? "full").trim().toLowerCase() === "skip"
    ? "skip"
    : "full";

/** When true (default), only one buy per token mint per session (prevents pyramiding). Toggle with 'b' key. */
const BUY_ONCE_RAW = (process.env.BUY_ONCE_PER_TOKEN ?? "true").toLowerCase();
export const buyOncePerToken =
  BUY_ONCE_RAW !== "false" && BUY_ONCE_RAW !== "0" && BUY_ONCE_RAW !== "no";
export const buyOnceAllowRebuyAfterFlat = parseEnvBool(
  process.env.BUY_ONCE_ALLOW_REBUY_AFTER_FLAT,
  false
);
/** Optional cap on repeated entries into the same token. Useful when BUY_ONCE_PER_TOKEN=false. */
export const maxBuysPerToken = parseOptionalPositiveInteger(process.env.MAX_BUYS_PER_TOKEN);
let MAX_SOL_PER_TOKEN = parseOptionalPositiveNumber(process.env.MAX_SOL_PER_TOKEN);
/** Optional cap on total SOL spent per token position. */
export let maxSolPerTokenLamports =
  MAX_SOL_PER_TOKEN != null ? Math.round(MAX_SOL_PER_TOKEN * LAMPORTS_PER_SOL) : null;
let MAX_SESSION_DEPLOYED_SOL = parseOptionalPositiveNumber(process.env.MAX_SESSION_DEPLOYED_SOL);
/** Optional cap on total gross SOL deployed into buys during this bot session. */
export let maxSessionDeployedLamports =
  MAX_SESSION_DEPLOYED_SOL != null ? Math.round(MAX_SESSION_DEPLOYED_SOL * LAMPORTS_PER_SOL) : null;
let MAX_OPEN_DEPLOYED_SOL = parseOptionalPositiveNumber(process.env.MAX_OPEN_DEPLOYED_SOL);
/** Optional cap on currently open SOL cost basis plus the next buy. Better for high-turnover copy sources. */
export let maxOpenDeployedLamports =
  MAX_OPEN_DEPLOYED_SOL != null ? Math.round(MAX_OPEN_DEPLOYED_SOL * LAMPORTS_PER_SOL) : null;

/**
 * Startup-only source wallet prefilter. When enabled, the bot builds an in-memory set of mints
 * the tracked wallet already held or recently bought before live mirroring starts, then skips
 * future buy signals for those mints. Hot path cost is one Set lookup.
 */
const SKIP_SOURCE_PREEXISTING_MINTS_RAW = (
  process.env.SKIP_SOURCE_PREEXISTING_MINTS ?? "false"
).toLowerCase();
export const skipSourcePreexistingMints =
  SKIP_SOURCE_PREEXISTING_MINTS_RAW === "true" ||
  SKIP_SOURCE_PREEXISTING_MINTS_RAW === "1" ||
  SKIP_SOURCE_PREEXISTING_MINTS_RAW === "yes";
export const sourcePreexistingScanTxLimit = Math.max(
  0,
  parseInt(process.env.SOURCE_PREEXISTING_SCAN_TX_LIMIT ?? "100", 10)
);

const FAILED_BUY_RECOVERY_RAW = (process.env.FAILED_BUY_RECOVERY ?? "NONE").toUpperCase();
export type FailedBuyRecovery = "NONE" | "JUPITER_ONCE";
export const failedBuyRecovery: FailedBuyRecovery =
  FAILED_BUY_RECOVERY_RAW === "JUPITER_ONCE" ? "JUPITER_ONCE" : "NONE";

const PREBUY_MORALIS_FILTER_ENABLED_RAW = (
  process.env.PREBUY_MORALIS_FILTER_ENABLED ?? "false"
).toLowerCase();
export const prebuyMoralisFilterEnabled =
  PREBUY_MORALIS_FILTER_ENABLED_RAW === "true" ||
  PREBUY_MORALIS_FILTER_ENABLED_RAW === "1" ||
  PREBUY_MORALIS_FILTER_ENABLED_RAW === "yes";
export const prebuyMoralisWindowMs = Math.max(
  60_000,
  parseInt(process.env.PREBUY_MORALIS_WINDOW_MS ?? "300000", 10)
);
export const prebuyMinVolumeUsd = Math.max(
  0,
  parseFloat(process.env.PREBUY_MIN_VOLUME_USD ?? "0")
);
export const prebuyMinMomentumPct = parseOptionalPositiveNumber(
  process.env.PREBUY_MIN_MOMENTUM_PCT
);
export const prebuyMinLiquidityUsd = parseOptionalPositiveNumber(
  process.env.PREBUY_MIN_LIQUIDITY_USD
);
export const prebuyMinMarketCapUsd = parseOptionalPositiveNumber(
  process.env.PREBUY_MIN_MARKET_CAP_USD
);
export const prebuyMaxMarketCapUsd = parseOptionalPositiveNumber(
  process.env.PREBUY_MAX_MARKET_CAP_USD
);
export const prebuyInferredMcFilterEnabled = parseEnvBool(
  process.env.PREBUY_INFERRED_MC_FILTER_ENABLED,
  false
);
export const prebuyInferredTokenSupply = Math.max(
  1,
  parseFloat(process.env.PREBUY_INFERRED_TOKEN_SUPPLY ?? "1000000000")
);
export const prebuyInferredDefaultTokenDecimals = Math.max(
  0,
  Math.min(18, parseInt(process.env.PREBUY_INFERRED_DEFAULT_TOKEN_DECIMALS ?? "6", 10))
);
export const localPumpfunMarketCapFilterEnabled = parseEnvBool(
  process.env.LOCAL_PUMPFUN_MARKET_CAP_FILTER_ENABLED,
  false
);
export let localPumpfunSolPriceUsd = parseOptionalPositiveNumber(
  process.env.LOCAL_PUMPFUN_SOL_PRICE_USD ?? process.env.SOL_PRICE_USD
);
export const localPumpfunSolPriceAutoEnabled = parseEnvBool(
  process.env.LOCAL_PUMPFUN_SOL_PRICE_AUTO_ENABLED,
  false
);
export const localPumpfunSolPriceUrl =
  process.env.LOCAL_PUMPFUN_SOL_PRICE_URL ??
  "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd";
export const localPumpfunSolPriceRefreshMs = Math.max(
  30_000,
  parseInt(process.env.LOCAL_PUMPFUN_SOL_PRICE_REFRESH_MS ?? "300000", 10)
);
export const localPumpfunSolPriceTimeoutMs = Math.max(
  500,
  parseInt(process.env.LOCAL_PUMPFUN_SOL_PRICE_TIMEOUT_MS ?? "2500", 10)
);
export function updateLocalPumpfunSolPriceUsd(value: number): boolean {
  if (!Number.isFinite(value) || value <= 0) return false;
  localPumpfunSolPriceUsd = value;
  process.env.LOCAL_PUMPFUN_SOL_PRICE_USD = value.toFixed(4);
  return true;
}
export const localPumpfunMinMarketCapSol = parseOptionalPositiveNumber(
  process.env.LOCAL_PUMPFUN_MIN_MARKET_CAP_SOL
);
export const localPumpfunMaxMarketCapSol = parseOptionalPositiveNumber(
  process.env.LOCAL_PUMPFUN_MAX_MARKET_CAP_SOL
);
export const prebuyMoralisTimeoutMs = Math.max(
  250,
  parseInt(process.env.PREBUY_MORALIS_TIMEOUT_MS ?? "1500", 10)
);
const PREBUY_FILTER_FAIL_OPEN_RAW = (process.env.PREBUY_FILTER_FAIL_OPEN ?? "false").toLowerCase();
export const prebuyFilterFailOpen =
  PREBUY_FILTER_FAIL_OPEN_RAW === "true" ||
  PREBUY_FILTER_FAIL_OPEN_RAW === "1" ||
  PREBUY_FILTER_FAIL_OPEN_RAW === "yes";

/** Optional cap on simultaneously open token positions. Empty/0 = no cap. */
export let maxOpenPositions = parseOptionalPositiveInteger(process.env.MAX_OPEN_POSITIONS);

/**
 * When true (default), fetch & display market cap from DexScreener whenever available.
 * Note: can be inaccurate for fresh pump.fun tokens (wrong pool picked, stale data).
 * Toggle with 'c' key.
 */
const SHOW_MC_RAW = (process.env.SHOW_MARKET_CAP ?? "true").toLowerCase();
export const showMarketCap =
  SHOW_MC_RAW !== "false" && SHOW_MC_RAW !== "0" && SHOW_MC_RAW !== "no";

const PNL_ACCOUNTING_BASIS_RAW = (process.env.PNL_ACCOUNTING_BASIS ?? "wallet").toLowerCase();
export const pnlAccountingBasis: "dex" | "wallet" =
  PNL_ACCOUNTING_BASIS_RAW === "wallet" || PNL_ACCOUNTING_BASIS_RAW === "cashflow"
    ? "wallet"
    : "dex";
export const useWalletPnlAccounting = pnlAccountingBasis === "wallet";

/** Skip pump.fun tokens identified as Mayhem Mode. Default off so the hot path has zero Mayhem-filter cost. */
const BLOCK_MAYHEM_RAW = (process.env.BLOCK_PUMPFUN_MAYHEM ?? "false").toLowerCase();
export const blockPumpfunMayhem =
  BLOCK_MAYHEM_RAW !== "false" && BLOCK_MAYHEM_RAW !== "0" && BLOCK_MAYHEM_RAW !== "no";

/** When true, skip pump.fun buys if Mayhem status cannot be verified. */
const MAYHEM_STRICT_RAW = (process.env.PUMPFUN_MAYHEM_STRICT ?? "false").toLowerCase();
export const pumpfunMayhemStrict =
  MAYHEM_STRICT_RAW === "true" || MAYHEM_STRICT_RAW === "1" || MAYHEM_STRICT_RAW === "yes";

const USE_PUMPFUN_TOKEN2022_DIRECT_SELL_RAW = (
  process.env.USE_PUMPFUN_TOKEN2022_DIRECT_SELL ?? "true"
).toLowerCase();
export const usePumpfunToken2022DirectSell =
  USE_PUMPFUN_TOKEN2022_DIRECT_SELL_RAW !== "false" &&
  USE_PUMPFUN_TOKEN2022_DIRECT_SELL_RAW !== "0" &&
  USE_PUMPFUN_TOKEN2022_DIRECT_SELL_RAW !== "no";

/** Telegram Bot Token for notifications. Get from @BotFather. */
export const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN ?? "";

/** Telegram Chat ID for notifications. Get from getUpdates API. */
export const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID ?? "";

/** Optional Telegram group/channel chat ID for bot mode/status broadcasts. */
export const TELEGRAM_BROADCAST_CHAT_ID =
  process.env.TELEGRAM_BROADCAST_CHAT_ID ?? "";

/** Tip amount in lamports (default 65_000 = 0.000065 SOL). Configurable via TIP_LAMPORTS. */
export let tipLamports = TIP_LAMPORTS;

/** Priority fee in microLamports per CU (default 50_000). Configurable via PRIORITY_FEE_MICRO_LAMPORTS. */
export let priorityFeeMicroLamports = PRIORITY_FEE_MICRO_LAMPORTS;

/** Mirror swap slippage in basis points. 500 = 5%. */
export let mirrorSlippageBps = MIRROR_SLIPPAGE_BPS;

/** One-time retry slippage for Jupiter 6001 slippage failures. 1500 = 15%. */
export const mirrorRetrySlippageBps = MIRROR_RETRY_SLIPPAGE_BPS;

/** Compute Unit Limit override. Set 0 to keep Jupiter's dynamic estimate. */
const COMPUTE_UNIT_LIMIT = parseInt(process.env.COMPUTE_UNIT_LIMIT ?? "1400000", 10);
export const computeUnitLimit = COMPUTE_UNIT_LIMIT;

/** Fast mode - skip transaction confirmation for maximum speed. Configurable via FAST_MODE. */
const FAST_MODE_RAW = (process.env.FAST_MODE ?? "false").toLowerCase();
export const fastMode = FAST_MODE_RAW === "true" || FAST_MODE_RAW === "1";

/** Confirmation polling timeout. Hot mirror trades should fail fast enough to re-quote/rebroadcast. */
export const confirmationTimeoutMs = Math.max(
  1_000,
  parseInt(process.env.CONFIRMATION_TIMEOUT_MS ?? "25000", 10)
);
/** If the signature never appears in status polling, treat it as dropped before the full timeout. */
export const confirmationNullStatusTimeoutMs = Math.max(
  1_000,
  parseInt(process.env.CONFIRMATION_NULL_STATUS_TIMEOUT_MS ?? "10000", 10)
);
/** One-shot rebuild/rebroadcast count for direct routes after a suspected dropped tx. */
export const confirmationRebroadcastRetries = Math.max(
  0,
  parseInt(process.env.CONFIRMATION_REBROADCAST_RETRIES ?? "1", 10)
);

/** Min buy in lamports. See MIN_BUY_BEHAVIOR for below-minimum copy trades. */
export let minBuyLamports = Math.round(MIN_BUY_SOL * LAMPORTS_PER_SOL);
/** Max buy in lamports (for copytrading: cap mirror size to this). */
export let maxBuyLamports = Math.round(MAX_BUY_SOL * LAMPORTS_PER_SOL);
/** Min/max USDC buy size in raw units (USDC has 6 decimals). */
export const minBuyUsdcRaw = Math.round(MIN_BUY_USDC * 1_000_000);
export const maxBuyUsdcRaw = Math.round(MAX_BUY_USDC * 1_000_000);
/** SOL reserve in lamports. Prevents wallet from going to 0 SOL. */
export let solReserveLamports = Math.round(SOL_RESERVE * LAMPORTS_PER_SOL);

export type FriendBotSettings = {
  trackedWallets: string[];
  minBuySol: number;
  maxBuySol: number;
  copyBuyPercentage: number;
  solReserveSol: number;
  tipLamports: number;
  priorityFeeMicroLamports: number;
  mirrorSlippageBps: number;
  mirrorSells: boolean;
  simulationMode: boolean;
  maxOpenPositions: number | null;
  maxSolPerTokenSol: number | null;
  maxSessionDeployedSol: number | null;
  maxOpenDeployedSol: number | null;
  maxBuyExecutionFeeSol: number | null;
  maxBuyExecutionFeePctOfBuy: number | null;
};

export function getFriendBotSettings(): FriendBotSettings {
  return {
    trackedWallets: trackedWallets.slice(),
    minBuySol: minBuyLamports / LAMPORTS_PER_SOL,
    maxBuySol: maxBuyLamports / LAMPORTS_PER_SOL,
    copyBuyPercentage,
    solReserveSol: solReserveLamports / LAMPORTS_PER_SOL,
    tipLamports,
    priorityFeeMicroLamports,
    mirrorSlippageBps,
    mirrorSells,
    simulationMode,
    maxOpenPositions: maxOpenPositions ?? null,
    maxSolPerTokenSol: maxSolPerTokenLamports != null ? maxSolPerTokenLamports / LAMPORTS_PER_SOL : null,
    maxSessionDeployedSol: maxSessionDeployedLamports != null ? maxSessionDeployedLamports / LAMPORTS_PER_SOL : null,
    maxOpenDeployedSol: maxOpenDeployedLamports != null ? maxOpenDeployedLamports / LAMPORTS_PER_SOL : null,
    maxBuyExecutionFeeSol: maxBuyExecutionFeeLamports != null ? maxBuyExecutionFeeLamports / LAMPORTS_PER_SOL : null,
    maxBuyExecutionFeePctOfBuy,
  };
}

function positiveFinite(value: number, fallback: number): number {
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

export function applyFriendBotSettings(settings: Partial<FriendBotSettings>): FriendBotSettings {
  if (settings.trackedWallets) {
    trackedWallets.splice(
      0,
      trackedWallets.length,
      ...settings.trackedWallets.map((w) => w.trim()).filter(Boolean)
    );
    process.env.TRACKED_WALLETS = trackedWallets.join(",");
  }

  MIN_BUY_SOL = positiveFinite(settings.minBuySol ?? MIN_BUY_SOL, MIN_BUY_SOL);
  MAX_BUY_SOL = positiveFinite(settings.maxBuySol ?? MAX_BUY_SOL, MAX_BUY_SOL);
  if (MAX_BUY_SOL < MIN_BUY_SOL) MAX_BUY_SOL = MIN_BUY_SOL;
  const nextCopyBuyPercentage = settings.copyBuyPercentage ?? COPY_BUY_PERCENTAGE;
  if (!Number.isFinite(nextCopyBuyPercentage)) {
    throw new Error("copyBuyPercentage must be a finite number");
  }
  if (nextCopyBuyPercentage < 0) {
    throw new Error("copyBuyPercentage cannot be negative");
  }
  COPY_BUY_PERCENTAGE = Math.min(100, nextCopyBuyPercentage);
  SOL_RESERVE = Math.max(0, settings.solReserveSol ?? SOL_RESERVE);
  TIP_LAMPORTS = Math.max(0, Math.round(settings.tipLamports ?? TIP_LAMPORTS));
  PRIORITY_FEE_MICRO_LAMPORTS = Math.max(
    0,
    Math.round(settings.priorityFeeMicroLamports ?? PRIORITY_FEE_MICRO_LAMPORTS)
  );
  MIRROR_SLIPPAGE_BPS = Math.max(1, Math.round(settings.mirrorSlippageBps ?? MIRROR_SLIPPAGE_BPS));
  if (typeof settings.mirrorSells === "boolean") mirrorSells = settings.mirrorSells;
  if (settings.maxOpenPositions !== undefined) maxOpenPositions = settings.maxOpenPositions != null ? Math.max(1, Math.round(settings.maxOpenPositions)) : null;
  if (settings.maxSolPerTokenSol !== undefined) MAX_SOL_PER_TOKEN = settings.maxSolPerTokenSol != null ? Math.max(0, settings.maxSolPerTokenSol) : null;
  if (settings.maxSessionDeployedSol !== undefined) MAX_SESSION_DEPLOYED_SOL = settings.maxSessionDeployedSol != null ? Math.max(0, settings.maxSessionDeployedSol) : null;
  if (settings.maxOpenDeployedSol !== undefined) MAX_OPEN_DEPLOYED_SOL = settings.maxOpenDeployedSol != null ? Math.max(0, settings.maxOpenDeployedSol) : null;
  if (settings.maxBuyExecutionFeeSol !== undefined) {
    MAX_BUY_EXECUTION_FEE_SOL =
      settings.maxBuyExecutionFeeSol != null ? Math.max(0, settings.maxBuyExecutionFeeSol) : null;
  }
  if (settings.maxBuyExecutionFeePctOfBuy !== undefined) {
    MAX_BUY_EXECUTION_FEE_PCT_OF_BUY =
      settings.maxBuyExecutionFeePctOfBuy != null ? Math.max(0, settings.maxBuyExecutionFeePctOfBuy) : 0;
  }

  tipLamports = TIP_LAMPORTS;
  priorityFeeMicroLamports = PRIORITY_FEE_MICRO_LAMPORTS;
  mirrorSlippageBps = MIRROR_SLIPPAGE_BPS;
  copyBuyPercentage = COPY_BUY_PERCENTAGE;
  minBuyLamports = Math.round(MIN_BUY_SOL * LAMPORTS_PER_SOL);
  maxBuyLamports = Math.round(MAX_BUY_SOL * LAMPORTS_PER_SOL);
  solReserveLamports = Math.round(SOL_RESERVE * LAMPORTS_PER_SOL);
  maxSolPerTokenLamports = MAX_SOL_PER_TOKEN != null && MAX_SOL_PER_TOKEN > 0 ? Math.round(MAX_SOL_PER_TOKEN * LAMPORTS_PER_SOL) : null;
  maxSessionDeployedLamports = MAX_SESSION_DEPLOYED_SOL != null && MAX_SESSION_DEPLOYED_SOL > 0 ? Math.round(MAX_SESSION_DEPLOYED_SOL * LAMPORTS_PER_SOL) : null;
  maxOpenDeployedLamports = MAX_OPEN_DEPLOYED_SOL != null && MAX_OPEN_DEPLOYED_SOL > 0 ? Math.round(MAX_OPEN_DEPLOYED_SOL * LAMPORTS_PER_SOL) : null;
  maxBuyExecutionFeeLamports =
    MAX_BUY_EXECUTION_FEE_SOL != null && MAX_BUY_EXECUTION_FEE_SOL > 0
      ? Math.round(MAX_BUY_EXECUTION_FEE_SOL * LAMPORTS_PER_SOL)
      : null;
  maxBuyExecutionFeePctOfBuy =
    MAX_BUY_EXECUTION_FEE_PCT_OF_BUY != null &&
    Number.isFinite(MAX_BUY_EXECUTION_FEE_PCT_OF_BUY) &&
    MAX_BUY_EXECUTION_FEE_PCT_OF_BUY >= 0
      ? MAX_BUY_EXECUTION_FEE_PCT_OF_BUY
      : null;
  if (typeof settings.simulationMode === "boolean") setSimulationMode(settings.simulationMode);

  process.env.MIN_BUY_SOL = String(MIN_BUY_SOL);
  process.env.MAX_BUY_SOL = String(MAX_BUY_SOL);
  process.env.COPY_BUY_PERCENTAGE = String(COPY_BUY_PERCENTAGE);
  process.env.SOL_RESERVE = String(SOL_RESERVE);
  process.env.TIP_LAMPORTS = String(TIP_LAMPORTS);
  process.env.PRIORITY_FEE_MICRO_LAMPORTS = String(PRIORITY_FEE_MICRO_LAMPORTS);
  process.env.MIRROR_SLIPPAGE_BPS = String(MIRROR_SLIPPAGE_BPS);
  process.env.MIRROR_SELLS = mirrorSells ? "true" : "false";
  process.env.SIMULATION_MODE = simulationMode ? "true" : "false";
  process.env.MAX_BUY_EXECUTION_FEE_SOL = MAX_BUY_EXECUTION_FEE_SOL != null ? String(MAX_BUY_EXECUTION_FEE_SOL) : "";
  process.env.MAX_BUY_EXECUTION_FEE_PCT_OF_BUY =
    maxBuyExecutionFeePctOfBuy != null ? String(maxBuyExecutionFeePctOfBuy) : "";
  process.env.RUST_HOT_SNIPER_MAX_EXECUTION_FEE_SOL =
    MAX_BUY_EXECUTION_FEE_SOL != null ? String(MAX_BUY_EXECUTION_FEE_SOL) : "";
  process.env.RUST_HOT_SNIPER_MAX_FEE_PCT_OF_BUY =
    maxBuyExecutionFeePctOfBuy != null ? String(maxBuyExecutionFeePctOfBuy) : "";

  return getFriendBotSettings();
}

/** Poll interval in ms. Lower = faster detection, more RPC load. Default 2500. */
export const pollIntervalMs = Math.max(
  500,
  parseInt(process.env.POLL_INTERVAL_MS ?? "2500", 10)
);

/** Optional risk exits. Percent values are measured against remaining cost basis. Empty/0 = disabled. */
export let stopLossPct = parseOptionalPositiveNumber(process.env.STOP_LOSS_PCT);
export let takeProfitPct = parseOptionalPositiveNumber(process.env.TAKE_PROFIT_PCT);
export let hybridExitEnabled = parseEnvBool(process.env.HYBRID_EXIT_ENABLED, false);
export let hybridExitTpPct = parseNonNegativeNumber(process.env.HYBRID_EXIT_TP_PCT, 30);
export let hybridExitTpSellPct = Math.max(
  0,
  Math.min(100, parseNonNegativeNumber(process.env.HYBRID_EXIT_TP_SELL_PCT, 50))
);
export let hybridExitSourceExitRemaining = parseEnvBool(
  process.env.HYBRID_EXIT_SOURCE_EXIT_REMAINING,
  true
);
export const sourceHoldExitEnabled = parseEnvBool(process.env.SOURCE_HOLD_EXIT_ENABLED, false);
export const sourceHoldExitIntervalMs = Math.max(
  1000,
  parseNonNegativeInteger(process.env.SOURCE_HOLD_EXIT_INTERVAL_MS, 10_000)
);
export const sourceHoldExitConsecutiveMisses = Math.max(
  1,
  parseNonNegativeInteger(process.env.SOURCE_HOLD_EXIT_CONSECUTIVE_MISSES, 2)
);
export const sourceHoldExitMinPositionAgeMs = Math.max(
  0,
  parseNonNegativeInteger(process.env.SOURCE_HOLD_EXIT_MIN_POSITION_AGE_MS, 15_000)
);

/**
 * Tiered risk-check cadence. Each position falls into one of three buckets per loop tick:
 *   - "fast": within riskFastWindowMs of the buy — every riskFastIntervalMs.
 *   - "near": last-known PnL projects to within riskNearTriggerPct points of TP/SL — every riskNearTriggerIntervalMs.
 *   - "normal": otherwise — every riskNormalIntervalMs.
 * Setting RISK_CHECK_INTERVAL_MS still works as a back-compat alias for the normal bucket.
 */
export const riskFastIntervalMs = Math.max(
  100,
  parseInt(process.env.RISK_FAST_INTERVAL_MS ?? "500", 10)
);
export const riskFastWindowMs = Math.max(
  0,
  parseInt(process.env.RISK_FAST_WINDOW_MS ?? "20000", 10)
);
export const riskNearTriggerIntervalMs = Math.max(
  100,
  parseInt(process.env.RISK_NEAR_TRIGGER_INTERVAL_MS ?? "400", 10)
);
export const riskNormalIntervalMs = Math.max(
  250,
  parseInt(
    process.env.RISK_NORMAL_INTERVAL_MS ?? process.env.RISK_CHECK_INTERVAL_MS ?? "1000",
    10
  )
);
/** Distance in PnL percentage points from a trigger that counts as "near". */
export const riskNearTriggerPct = Math.max(
  0,
  parseFloat(process.env.RISK_NEAR_TRIGGER_PCT ?? "8")
);
/**
 * Worst-case projected PnL drift per second since the last Jupiter quote, in percentage points.
 * Used to decide whether we can safely skip a Jupiter quote this tick: if (lastPnl ± drift × elapsed)
 * cannot have crossed a trigger, we don't quote. Higher = more conservative (more quotes).
 */
export const riskDriftPctPerSec = Math.max(
  0,
  parseFloat(process.env.RISK_DRIFT_PCT_PER_SEC ?? "3")
);
/** Legacy field surfaced for status display. Kept equal to the normal-bucket interval. */
export const riskCheckIntervalMs = riskNormalIntervalMs;
/** Maximum open positions to quote per risk-check cycle. Prevents risk checks from starving buy quotes. */
export const maxRiskQuotesPerCheck = Math.max(
  1,
  parseInt(process.env.MAX_RISK_QUOTES_PER_CHECK ?? "2", 10)
);
/**
 * Stop-loss grace period after a buy. Take-profit can still trigger immediately.
 * Default 3500ms protects against quote noise in the first tick or two without disarming SL when it matters most.
 */
export let minHoldBeforeStopLossMs = Math.max(
  0,
  parseNonNegativeInteger(process.env.MIN_HOLD_BEFORE_STOP_LOSS_MS, 3500)
);
export let riskControlsEnabled = stopLossPct != null || takeProfitPct != null || hybridExitEnabled || profitLadderEnabled;

export type FriendRiskSettings = {
  stopLossPct?: string | number | null;
  takeProfitPct?: string | number | null;
  minHoldBeforeStopLossMs?: string | number | null;
  hybridExitEnabled?: boolean | string | number | null;
  hybridExitTpPct?: string | number | null;
  hybridExitTpSellPct?: string | number | null;
  hybridExitSourceExitRemaining?: boolean | string | number | null;
};

function riskNumber(value: string | number | null | undefined): string | undefined {
  if (value == null) return undefined;
  return String(value).trim();
}

export function applyFriendRiskSettings(settings: FriendRiskSettings): void {
  const nextStopLoss = riskNumber(settings.stopLossPct);
  if (nextStopLoss !== undefined) {
    process.env.STOP_LOSS_PCT = nextStopLoss;
    stopLossPct = parseOptionalPositiveNumber(nextStopLoss);
  }
  const nextTakeProfit = riskNumber(settings.takeProfitPct);
  if (nextTakeProfit !== undefined) {
    process.env.TAKE_PROFIT_PCT = nextTakeProfit;
    takeProfitPct = parseOptionalPositiveNumber(nextTakeProfit);
  }
  const nextMinHold = riskNumber(settings.minHoldBeforeStopLossMs);
  if (nextMinHold !== undefined) {
    process.env.MIN_HOLD_BEFORE_STOP_LOSS_MS = nextMinHold;
    minHoldBeforeStopLossMs = Math.max(0, parseNonNegativeInteger(nextMinHold, minHoldBeforeStopLossMs));
  }
  if (settings.hybridExitEnabled != null) {
    process.env.HYBRID_EXIT_ENABLED = String(settings.hybridExitEnabled);
    hybridExitEnabled = parseEnvBool(process.env.HYBRID_EXIT_ENABLED, false);
  }
  const nextHybridTp = riskNumber(settings.hybridExitTpPct);
  if (nextHybridTp !== undefined) {
    process.env.HYBRID_EXIT_TP_PCT = nextHybridTp;
    hybridExitTpPct = parseNonNegativeNumber(nextHybridTp, hybridExitTpPct);
  }
  const nextHybridSell = riskNumber(settings.hybridExitTpSellPct);
  if (nextHybridSell !== undefined) {
    process.env.HYBRID_EXIT_TP_SELL_PCT = nextHybridSell;
    hybridExitTpSellPct = Math.max(0, Math.min(100, parseNonNegativeNumber(nextHybridSell, hybridExitTpSellPct)));
  }
  if (settings.hybridExitSourceExitRemaining != null) {
    process.env.HYBRID_EXIT_SOURCE_EXIT_REMAINING = String(settings.hybridExitSourceExitRemaining);
    hybridExitSourceExitRemaining = parseEnvBool(process.env.HYBRID_EXIT_SOURCE_EXIT_REMAINING, true);
  }
  riskControlsEnabled = stopLossPct != null || takeProfitPct != null || hybridExitEnabled || profitLadderEnabled;
}

/** Shared Jupiter quote throttle/backoff for buys, sells, and risk checks. */
export const jupiterQuoteMinGapMs = Math.max(
  0,
  parseInt(process.env.JUPITER_QUOTE_MIN_GAP_MS ?? "650", 10)
);
export const jupiterQuoteRateLimitCooldownMs = Math.max(
  0,
  parseInt(process.env.JUPITER_QUOTE_429_COOLDOWN_MS ?? "3000", 10)
);
export const jupiterQuoteRetries = Math.max(
  0,
  parseInt(process.env.JUPITER_QUOTE_RETRIES ?? "1", 10)
);
/** Fallback max account counts for Jupiter routes that exceed the Solana packet limit. */
export const jupiterOversizeMaxAccounts = (process.env.JUPITER_OVERSIZE_MAX_ACCOUNTS ?? "48,40,32")
  .split(",")
  .map((v) => parseInt(v.trim(), 10))
  .filter((v) => Number.isFinite(v) && v > 0);

/** Simulation/dry-run mode: no transactions sent, only log what would be done. Set SIMULATION_MODE=true or DRY_RUN=true. */
const SIMULATION_RAW =
  process.env.SIMULATION_MODE ?? process.env.DRY_RUN ?? "";
export let simulationMode =
  SIMULATION_RAW === "true" ||
  SIMULATION_RAW === "1" ||
  SIMULATION_RAW.toLowerCase() === "yes";

export function setSimulationMode(enabled: boolean): void {
  simulationMode = enabled;
}

/** In simulation mode, use this as your starting SOL balance for display (e.g. 10 = 10 SOL). Set SIMULATION_SOL_BALANCE or STARTING_SOL_BALANCE. */
const SIM_SOL_RAW =
  process.env.SIMULATION_SOL_BALANCE ?? process.env.STARTING_SOL_BALANCE ?? "";
export const simulationSolBalance: number | undefined = SIM_SOL_RAW
  ? (() => {
      const n = parseFloat(SIM_SOL_RAW);
      return Number.isFinite(n) && n >= 0 ? n : undefined;
    })()
  : undefined;

const SHADOW_EXECUTION_RAW = (process.env.SHADOW_EXECUTION_MODE ?? "true").toLowerCase();
export const shadowExecutionMode =
  SHADOW_EXECUTION_RAW !== "false" &&
  SHADOW_EXECUTION_RAW !== "0" &&
  SHADOW_EXECUTION_RAW !== "no";
export const shadowLatencyMs = Math.max(0, parseInt(process.env.SHADOW_LATENCY_MS ?? "250", 10));
export const shadowEntrySlippagePct = Math.max(
  0,
  parseFloat(process.env.SHADOW_ENTRY_SLIPPAGE_PCT ?? "3")
);
export const shadowExitSlippagePct = Math.max(
  0,
  parseFloat(process.env.SHADOW_EXIT_SLIPPAGE_PCT ?? "5")
);
export const shadowTxFeeSol = Math.max(0, parseFloat(process.env.SHADOW_TX_FEE_SOL ?? "0"));
export const shadowFailureRatePct = Math.max(
  0,
  Math.min(100, parseFloat(process.env.SHADOW_FAILURE_RATE_PCT ?? "0"))
);
const SHADOW_RPC_SIM_RAW = (process.env.SHADOW_RPC_SIMULATE_TX ?? "true").toLowerCase();
export const shadowRpcSimulateTx =
  SHADOW_RPC_SIM_RAW !== "false" &&
  SHADOW_RPC_SIM_RAW !== "0" &&
  SHADOW_RPC_SIM_RAW !== "no";
const SHADOW_RPC_FAIL_OPEN_RAW = (process.env.SHADOW_RPC_SIM_FAIL_OPEN ?? "true").toLowerCase();
export const shadowRpcSimFailOpen =
  SHADOW_RPC_FAIL_OPEN_RAW !== "false" &&
  SHADOW_RPC_FAIL_OPEN_RAW !== "0" &&
  SHADOW_RPC_FAIL_OPEN_RAW !== "no";

/**
 * Jito tip accounts (mainnet-beta). Required by Jito's BlockEngine to give your bundle MEV
 * priority. Verified via `getTipAccounts` on https://ny.mainnet.block-engine.jito.wtf —
 * Jito rotates these, so refresh from that endpoint if Jito starts rejecting bundles.
 */
export const JITO_TIP_ACCOUNTS = [
  "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
  "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
  "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
  "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
  "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
  "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
  "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
  "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
];

/**
 * Helius Sender tip accounts. **Different from Jito's list — zero overlap.** Helius Sender
 * rejects any tx that doesn't transfer >= HELIUS_TIP_MIN_LAMPORTS to one of these.
 *
 * Why both lists matter: with USE_JITO_DIRECT=true and USE_JITO_FALLBACK=true, the bot tries
 * Jito first then falls back to Helius. The tx must satisfy BOTH validators' tip rules to be
 * accepted by whichever path runs — so we include two tip transfers, one to each list.
 *
 * Source: rejection message from Helius Sender itself when tx omits a Helius tip.
 */
export const HELIUS_TIP_ACCOUNTS = [
  "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta",
  "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
  "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ",
  "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or",
  "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn",
  "2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD",
  "D1Mc6j9xQWgR1o1Z7yU5nVVXFQiAYx7FG9AW1aVfwrUM",
  "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE",
  "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
  "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF",
  "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey",
];

/** Helius Sender's minimum tip in lamports. Anything less and Sender rejects the request. */
export const HELIUS_TIP_MIN_LAMPORTS = 200_000;

/**
 * Legacy alias kept so existing callers compile. Resolves to the *active* primary list — Jito
 * when USE_JITO_DIRECT is on, Helius otherwise — for code paths that only know about one tip.
 */
export const TIP_ACCOUNTS: readonly string[] = JITO_TIP_ACCOUNTS;

/** Connection to Helius staked RPC. Use for getLatestBlockhash, getAddressLookupTable, and any read-only RPC. */
export const readConnection = new Connection(readRpcUrl, { disableRetryOnRateLimit: true });

/** Connection wired to Helius WebSocket endpoint. Use for onLogs / logsSubscribe. Falls back to readRpcUrl if WS not set. */
export const wsConnection = websocketUrl
  ? new Connection(readRpcUrl, { wsEndpoint: websocketUrl, commitment: "confirmed", disableRetryOnRateLimit: true })
  : readConnection;

/** Connection to Sender endpoint. Use for sendTransaction (via fetch) and all confirmation (getSignatureStatuses). */
export const sendConnection = new Connection(senderEndpointUrl, { disableRetryOnRateLimit: true });

export function getPrivateKeyBase58(): string {
  const key = process.env.PRIVATE_KEY_BASE58;
  if (!key?.length) {
    throw new Error("PRIVATE_KEY_BASE58 is not set in environment");
  }
  return key;
}
