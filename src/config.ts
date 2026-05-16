import "dotenv/config";
import { Connection, LAMPORTS_PER_SOL } from "@solana/web3.js";

const HELIUS_API_KEY = process.env.HELIUS_API_KEY ?? "";
const SENDER_ENDPOINT =
  process.env.SENDER_ENDPOINT ?? "http://ewr-sender.helius-rpc.com/fast";
const SECURE_RPC_URL = process.env.SECURE_RPC_URL ?? "";
const TIP_LAMPORTS = parseInt(
  process.env.TIP_LAMPORTS ?? "65000",
  10
) as number;
const PRIORITY_FEE_MICRO_LAMPORTS = parseInt(
  process.env.PRIORITY_FEE_MICRO_LAMPORTS ?? "50000",
  10
) as number;
const MIRROR_SLIPPAGE_BPS = parseInt(
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

/** Tracked wallet addresses (comma-separated in TRACKED_WALLETS). */
const TRACKED_WALLETS_RAW = process.env.TRACKED_WALLETS ?? "";
export const trackedWallets: string[] = TRACKED_WALLETS_RAW
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

/** Min buy size in SOL. Mirror trades below this are skipped. Default 0.01 SOL. */
const MIN_BUY_SOL = parseFloat(process.env.MIN_BUY_SOL ?? "0.01");
/** Max buy size in SOL. Mirror trades are capped to this. Default 1 SOL. */
const MAX_BUY_SOL = parseFloat(process.env.MAX_BUY_SOL ?? "1");
/** SOL reserve amount to keep for gas fees. Prevents wallet from going to 0 SOL. Default 0.1 SOL. */
const SOL_RESERVE = parseFloat(process.env.SOL_RESERVE ?? "0.1");

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
type TrailingStep = { peakPct: number; floorPct: number };
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
export const trailingSteps = parseTrailingSteps(process.env.TRAILING_STEPS);
export const trailingEnabled = trailingSteps.length > 0;

/**
 * When true (default), buys whose output mint is a pre-graduation pump.fun bonding-curve token
 * are routed directly to the pump.fun program — bypassing Jupiter entirely. Falls back to Jupiter
 * for graduated tokens or when curve detection fails.
 */
const PUMPFUN_DIRECT_RAW = (process.env.PUMPFUN_DIRECT_ENABLED ?? "true").toLowerCase();
export const pumpfunDirectEnabled =
  PUMPFUN_DIRECT_RAW !== "false" && PUMPFUN_DIRECT_RAW !== "0" && PUMPFUN_DIRECT_RAW !== "no";

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

/** Helius staked RPC URL for read-only operations (blockhash, ALTs). */
export const readRpcUrl =
  SECURE_RPC_URL.length > 0
    ? SECURE_RPC_URL
    : HELIUS_API_KEY.length > 0
    ? `https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}`
    : "https://mainnet.helius-rpc.com";

/** Sender endpoint URL for sendTransaction and confirmation polling. */
export const senderEndpointUrl = SENDER_ENDPOINT;

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
const JITO_REGION = (process.env.JITO_REGION ?? "").toLowerCase().trim();
// Jito's regional block engines. Each forwards to the same global validator set; the regional
// hostname just gives you a closer edge ingress. Empty string = use the regionless global.
const JITO_REGIONS: Record<string, string> = {
  "": "https://mainnet.block-engine.jito.wtf",
  ny: "https://ny.mainnet.block-engine.jito.wtf",
  amsterdam: "https://amsterdam.mainnet.block-engine.jito.wtf",
  frankfurt: "https://frankfurt.mainnet.block-engine.jito.wtf",
  tokyo: "https://tokyo.mainnet.block-engine.jito.wtf",
  "slc": "https://slc.mainnet.block-engine.jito.wtf",
};
export const jitoBundleUrl =
  `${JITO_REGIONS[JITO_REGION] ?? JITO_REGIONS[""]}/api/v1/bundles`;

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
export const shyftRabbitStreamUrl = process.env.SHYFT_RABBITSTREAM_URL ?? "";

/** When false, the bot ignores source-wallet sells (you exit manually via 'e' or 'a' keys). Default true. */
const MIRROR_SELLS_RAW = (process.env.MIRROR_SELLS ?? "true").toLowerCase();
export const mirrorSells =
  MIRROR_SELLS_RAW !== "false" && MIRROR_SELLS_RAW !== "0" && MIRROR_SELLS_RAW !== "no";

/** When true (default), only one buy per token mint per session (prevents pyramiding). Toggle with 'b' key. */
const BUY_ONCE_RAW = (process.env.BUY_ONCE_PER_TOKEN ?? "true").toLowerCase();
export const buyOncePerToken =
  BUY_ONCE_RAW !== "false" && BUY_ONCE_RAW !== "0" && BUY_ONCE_RAW !== "no";

/** Optional cap on simultaneously open token positions. Empty/0 = no cap. */
export const maxOpenPositions = parseOptionalPositiveInteger(process.env.MAX_OPEN_POSITIONS);

/**
 * When true (default), fetch & display market cap from DexScreener whenever available.
 * Note: can be inaccurate for fresh pump.fun tokens (wrong pool picked, stale data).
 * Toggle with 'c' key.
 */
const SHOW_MC_RAW = (process.env.SHOW_MARKET_CAP ?? "true").toLowerCase();
export const showMarketCap =
  SHOW_MC_RAW !== "false" && SHOW_MC_RAW !== "0" && SHOW_MC_RAW !== "no";

/** Skip pump.fun tokens identified as Mayhem Mode. Default off so the hot path has zero Mayhem-filter cost. */
const BLOCK_MAYHEM_RAW = (process.env.BLOCK_PUMPFUN_MAYHEM ?? "false").toLowerCase();
export const blockPumpfunMayhem =
  BLOCK_MAYHEM_RAW !== "false" && BLOCK_MAYHEM_RAW !== "0" && BLOCK_MAYHEM_RAW !== "no";

/** When true, skip pump.fun buys if Mayhem status cannot be verified. */
const MAYHEM_STRICT_RAW = (process.env.PUMPFUN_MAYHEM_STRICT ?? "false").toLowerCase();
export const pumpfunMayhemStrict =
  MAYHEM_STRICT_RAW === "true" || MAYHEM_STRICT_RAW === "1" || MAYHEM_STRICT_RAW === "yes";

/** Telegram Bot Token for notifications. Get from @BotFather. */
export const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN ?? "";

/** Telegram Chat ID for notifications. Get from getUpdates API. */
export const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID ?? "";

/** Optional Telegram group/channel chat ID for bot mode/status broadcasts. */
export const TELEGRAM_BROADCAST_CHAT_ID =
  process.env.TELEGRAM_BROADCAST_CHAT_ID ?? "";

/** Tip amount in lamports (default 65_000 = 0.000065 SOL). Configurable via TIP_LAMPORTS. */
export const tipLamports = TIP_LAMPORTS;

/** Priority fee in microLamports per CU (default 50_000). Configurable via PRIORITY_FEE_MICRO_LAMPORTS. */
export const priorityFeeMicroLamports = PRIORITY_FEE_MICRO_LAMPORTS;

/** Mirror swap slippage in basis points. 500 = 5%. */
export const mirrorSlippageBps = MIRROR_SLIPPAGE_BPS;

/** One-time retry slippage for Jupiter 6001 slippage failures. 1500 = 15%. */
export const mirrorRetrySlippageBps = MIRROR_RETRY_SLIPPAGE_BPS;

/** Compute Unit Limit override. Set 0 to keep Jupiter's dynamic estimate. */
const COMPUTE_UNIT_LIMIT = parseInt(process.env.COMPUTE_UNIT_LIMIT ?? "1400000", 10);
export const computeUnitLimit = COMPUTE_UNIT_LIMIT;

/** Fast mode - skip transaction confirmation for maximum speed. Configurable via FAST_MODE. */
const FAST_MODE_RAW = (process.env.FAST_MODE ?? "false").toLowerCase();
export const fastMode = FAST_MODE_RAW === "true" || FAST_MODE_RAW === "1";

/** Min buy in lamports (for copytrading: skip mirrors below this). */
export const minBuyLamports = Math.round(MIN_BUY_SOL * LAMPORTS_PER_SOL);
/** Max buy in lamports (for copytrading: cap mirror size to this). */
export const maxBuyLamports = Math.round(MAX_BUY_SOL * LAMPORTS_PER_SOL);
/** SOL reserve in lamports. Prevents wallet from going to 0 SOL. */
export const solReserveLamports = Math.round(SOL_RESERVE * LAMPORTS_PER_SOL);

/** Poll interval in ms. Lower = faster detection, more RPC load. Default 2500. */
export const pollIntervalMs = Math.max(
  500,
  parseInt(process.env.POLL_INTERVAL_MS ?? "2500", 10)
);

/** Optional risk exits. Percent values are measured against remaining cost basis. Empty/0 = disabled. */
export const stopLossPct = parseOptionalPositiveNumber(process.env.STOP_LOSS_PCT);
export const takeProfitPct = parseOptionalPositiveNumber(process.env.TAKE_PROFIT_PCT);

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
export const minHoldBeforeStopLossMs = Math.max(
  0,
  parseInt(process.env.MIN_HOLD_BEFORE_STOP_LOSS_MS ?? "3500", 10)
);
export const riskControlsEnabled = stopLossPct != null || takeProfitPct != null;

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
export const readConnection = new Connection(readRpcUrl);

/** Connection wired to Helius WebSocket endpoint. Use for onLogs / logsSubscribe. Falls back to readRpcUrl if WS not set. */
export const wsConnection = websocketUrl
  ? new Connection(readRpcUrl, { wsEndpoint: websocketUrl, commitment: "confirmed" })
  : readConnection;

/** Connection to Sender endpoint. Use for sendTransaction (via fetch) and all confirmation (getSignatureStatuses). */
export const sendConnection = new Connection(senderEndpointUrl);

export function getPrivateKeyBase58(): string {
  const key = process.env.PRIVATE_KEY_BASE58;
  if (!key?.length) {
    throw new Error("PRIVATE_KEY_BASE58 is not set in environment");
  }
  return key;
}
