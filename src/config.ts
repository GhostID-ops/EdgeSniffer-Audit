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

/** Helius staked RPC URL for read-only operations (blockhash, ALTs). */
export const readRpcUrl =
  SECURE_RPC_URL.length > 0
    ? SECURE_RPC_URL
    : HELIUS_API_KEY.length > 0
    ? `https://mainnet.helius-rpc.com/?api-key=${HELIUS_API_KEY}`
    : "https://mainnet.helius-rpc.com";

/** Sender endpoint URL for sendTransaction and confirmation polling. */
export const senderEndpointUrl = SENDER_ENDPOINT;

/** Helius enhanced transactions API (parse transactions). POST with { transactions: [sigs] }. */
export const heliusEnhancedTransactionsUrl =
  HELIUS_API_KEY.length > 0
    ? `https://api-mainnet.helius-rpc.com/v0/transactions?api-key=${HELIUS_API_KEY}`
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

/** When false, the bot ignores source-wallet sells (you exit manually via 'e' or 'a' keys). Default true. */
const MIRROR_SELLS_RAW = (process.env.MIRROR_SELLS ?? "true").toLowerCase();
export const mirrorSells =
  MIRROR_SELLS_RAW !== "false" && MIRROR_SELLS_RAW !== "0" && MIRROR_SELLS_RAW !== "no";

/** When true (default), only one buy per token mint per session (prevents pyramiding). Toggle with 'b' key. */
const BUY_ONCE_RAW = (process.env.BUY_ONCE_PER_TOKEN ?? "true").toLowerCase();
export const buyOncePerToken =
  BUY_ONCE_RAW !== "false" && BUY_ONCE_RAW !== "0" && BUY_ONCE_RAW !== "no";

/**
 * When true (default), fetch & display market cap from DexScreener whenever available.
 * Note: can be inaccurate for fresh pump.fun tokens (wrong pool picked, stale data).
 * Toggle with 'c' key.
 */
const SHOW_MC_RAW = (process.env.SHOW_MARKET_CAP ?? "true").toLowerCase();
export const showMarketCap =
  SHOW_MC_RAW !== "false" && SHOW_MC_RAW !== "0" && SHOW_MC_RAW !== "no";

/** Telegram Bot Token for notifications. Get from @BotFather. */
export const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN ?? "";

/** Telegram Chat ID for notifications. Get from getUpdates API. */
export const TELEGRAM_CHAT_ID = process.env.TELEGRAM_CHAT_ID ?? "";

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

/** Simulation/dry-run mode: no transactions sent, only log what would be done. Set SIMULATION_MODE=true or DRY_RUN=true. */
const SIMULATION_RAW =
  process.env.SIMULATION_MODE ?? process.env.DRY_RUN ?? "";
export const simulationMode =
  SIMULATION_RAW === "true" ||
  SIMULATION_RAW === "1" ||
  SIMULATION_RAW.toLowerCase() === "yes";

/** In simulation mode, use this as your starting SOL balance for display (e.g. 10 = 10 SOL). Set SIMULATION_SOL_BALANCE or STARTING_SOL_BALANCE. */
const SIM_SOL_RAW =
  process.env.SIMULATION_SOL_BALANCE ?? process.env.STARTING_SOL_BALANCE ?? "";
export const simulationSolBalance: number | undefined = SIM_SOL_RAW
  ? (() => {
      const n = parseFloat(SIM_SOL_RAW);
      return Number.isFinite(n) && n >= 0 ? n : undefined;
    })()
  : undefined;

/** Designated Jito tip accounts (mainnet-beta). */
export const TIP_ACCOUNTS = [
  "4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE",
  "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ",
  "9bnz4RShgq1hAnLnZbP8kbgBg1kEmcJBYQq3gQbmnSta",
  "5VY91ws6B2hMmBFRsXkoAAdsPHBJwRfBht4DXox3xkwn",
  "2nyhqdwKcJZR2vcqCyrYsaPVdAnFoJjiksCXJ7hfEYgD",
  "2q5pghRs6arqVjRvT5gfgWfWcHWmw1ZuCzphgd5KfWGJ",
  "wyvPkWjVZz1M8fHQnMMCDTQDbkManefNNhweYk5WkcF",
  "3KCKozbAaF75qEU33jtzozcJ29yJuaLJTy2jFdzUY8bT",
  "4vieeGHPYPG2MmyPRcYjdiDmmhN3ww7hsFNap8pVN3Ey",
  "4TQLFNWK8AovT1gFvda5jfw2oJeRMKEmw7aH6MGBJ3or",
];

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
