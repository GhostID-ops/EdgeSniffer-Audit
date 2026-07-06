import { existsSync, readFileSync, writeFileSync } from "node:fs";
import bs58 from "bs58";
import { Keypair, PublicKey } from "@solana/web3.js";
import {
  applyFriendRiskSettings,
  applyFriendBotSettings,
  getFriendBotSettings,
  type FriendBotSettings,
} from "./config.js";

import { envPath } from "./runtimePaths.js";

const SETTINGS_KEYS = [
  "TRACKED_WALLETS",
  "MIN_BUY_SOL",
  "MAX_BUY_SOL",
  "COPY_BUY_PERCENTAGE",
  "SOL_RESERVE",
  "TIP_LAMPORTS",
  "PRIORITY_FEE_MICRO_LAMPORTS",
  "MIRROR_SLIPPAGE_BPS",
  "MIRROR_SELLS",
  "SIMULATION_MODE",
  "MAX_OPEN_POSITIONS",
  "MAX_SOL_PER_TOKEN",
  "MAX_SESSION_DEPLOYED_SOL",
  "MAX_OPEN_DEPLOYED_SOL",
  "MAX_BUY_EXECUTION_FEE_SOL",
] as const;

type SettingsKey = (typeof SETTINGS_KEYS)[number];

const RISK_SETTINGS_KEYS = [
  "STOP_LOSS_PCT",
  "TAKE_PROFIT_PCT",
  "MIN_HOLD_BEFORE_STOP_LOSS_MS",
  "HYBRID_EXIT_ENABLED",
  "HYBRID_EXIT_TP_PCT",
  "HYBRID_EXIT_TP_SELL_PCT",
  "HYBRID_EXIT_SOURCE_EXIT_REMAINING",
  "PROFIT_LADDER_ENABLED",
  "PROFIT_LADDER_STEPS",
] as const;

function assertPublicKey(value: string): void {
  try {
    new PublicKey(value);
  } catch {
    throw new Error(`Invalid wallet address: ${value}`);
  }
}

function numberFromBody(body: Record<string, unknown>, key: string, fallback: number): number {
  const raw = body[key];
  if (raw == null || raw === "") return fallback;
  const n = typeof raw === "number" ? raw : parseFloat(String(raw));
  if (!Number.isFinite(n)) throw new Error(`${key} must be a number`);
  return n;
}

function optionalNumberFromBody(body: Record<string, unknown>, key: string, fallback: number | null): number | null {
  const raw = body[key];
  if (raw == null) return fallback;
  const text = String(raw).trim();
  if (text === "") return null;
  const n = typeof raw === "number" ? raw : parseFloat(text);
  if (!Number.isFinite(n)) throw new Error(`${key} must be a number`);
  return n;
}

function boolFromBody(body: Record<string, unknown>, key: string, fallback: boolean): boolean {
  const raw = body[key];
  if (raw == null || raw === "") return fallback;
  if (typeof raw === "boolean") return raw;
  return ["true", "1", "yes", "on"].includes(String(raw).toLowerCase());
}

function positiveEnvNumber(key: string): number | null {
  const raw = process.env[key];
  if (raw == null || raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export function normalizeFriendSettings(body: Record<string, unknown>): FriendBotSettings {
  const current = getFriendBotSettings();
  const walletsRaw = Array.isArray(body.trackedWallets)
    ? body.trackedWallets.join("\n")
    : String(body.trackedWallets ?? current.trackedWallets.join("\n"));
  const wallets = walletsRaw
    .split(/[\n,]+/)
    .map((w) => w.trim())
    .filter(Boolean);
  for (const wallet of wallets) assertPublicKey(wallet);

  const settings: FriendBotSettings = {
    trackedWallets: wallets,
    minBuySol: numberFromBody(body, "minBuySol", current.minBuySol),
    maxBuySol: numberFromBody(body, "maxBuySol", current.maxBuySol),
    copyBuyPercentage: numberFromBody(body, "copyBuyPercentage", current.copyBuyPercentage),
    solReserveSol: numberFromBody(body, "solReserveSol", current.solReserveSol),
    tipLamports: Math.round(numberFromBody(body, "tipLamports", current.tipLamports)),
    priorityFeeMicroLamports: Math.round(
      numberFromBody(body, "priorityFeeMicroLamports", current.priorityFeeMicroLamports)
    ),
    mirrorSlippageBps: Math.round(numberFromBody(body, "mirrorSlippageBps", current.mirrorSlippageBps)),
    mirrorSells: boolFromBody(body, "mirrorSells", current.mirrorSells),
    simulationMode: boolFromBody(body, "simulationMode", current.simulationMode),
    maxOpenPositions: optionalNumberFromBody(body, "maxOpenPositions", current.maxOpenPositions),
    maxSolPerTokenSol: optionalNumberFromBody(body, "maxSolPerTokenSol", current.maxSolPerTokenSol),
    maxSessionDeployedSol: optionalNumberFromBody(body, "maxSessionDeployedSol", current.maxSessionDeployedSol),
    maxOpenDeployedSol: optionalNumberFromBody(body, "maxOpenDeployedSol", current.maxOpenDeployedSol),
    maxBuyExecutionFeeSol: optionalNumberFromBody(body, "maxBuyExecutionFeeSol", current.maxBuyExecutionFeeSol),
  };

  if (!settings.simulationMode) {
    const maxLiveBuySol = positiveEnvNumber("FRIEND_MAX_LIVE_BUY_SOL");
    if (maxLiveBuySol != null && maxLiveBuySol > 0) {
      settings.maxBuySol = Math.min(settings.maxBuySol, maxLiveBuySol);
      settings.minBuySol = Math.min(settings.minBuySol, settings.maxBuySol);
    }
    const maxLiveTipLamports = positiveEnvNumber("FRIEND_MAX_LIVE_TIP_LAMPORTS");
    if (maxLiveTipLamports != null) {
      settings.tipLamports = Math.min(settings.tipLamports, Math.round(maxLiveTipLamports));
    }
    const maxLivePriorityFee = positiveEnvNumber("FRIEND_MAX_LIVE_PRIORITY_FEE_MICRO_LAMPORTS");
    if (maxLivePriorityFee != null) {
      settings.priorityFeeMicroLamports = Math.min(
        settings.priorityFeeMicroLamports,
        Math.round(maxLivePriorityFee)
      );
    }
  }

  if (settings.minBuySol <= 0) throw new Error("Minimum buy must be greater than 0 SOL");
  if (settings.maxBuySol < settings.minBuySol) {
    throw new Error("Maximum buy must be greater than or equal to minimum buy");
  }
  if (settings.copyBuyPercentage < 0) throw new Error("Copy buy percentage cannot be negative");
  if (settings.copyBuyPercentage > 100) settings.copyBuyPercentage = 100;
  if (settings.solReserveSol < 0) throw new Error("SOL reserve cannot be negative");
  if (settings.maxOpenPositions != null && settings.maxOpenPositions < 1) throw new Error("Max open positions must be at least 1 or blank");
  if (settings.maxSolPerTokenSol != null && settings.maxSolPerTokenSol < 0) throw new Error("Max per-token deployed SOL cannot be negative");
  if (settings.maxSessionDeployedSol != null && settings.maxSessionDeployedSol < 0) throw new Error("Max session deployed SOL cannot be negative");
  if (settings.maxOpenDeployedSol != null && settings.maxOpenDeployedSol < 0) throw new Error("Max open deployed SOL cannot be negative");
  if (settings.maxBuyExecutionFeeSol != null && settings.maxBuyExecutionFeeSol < 0) throw new Error("Max fee budget cannot be negative");
  if (settings.tipLamports < 0) throw new Error("Tip cannot be negative");
  if (settings.priorityFeeMicroLamports < 0) throw new Error("Priority fee cannot be negative");
  if (settings.mirrorSlippageBps < 1 || settings.mirrorSlippageBps > 10000) {
    throw new Error("Slippage must be between 1 and 10000 bps");
  }

  return settings;
}

function setEnvLine(lines: string[], key: string, value: string): string[] {
  const escaped = value.includes(" ") ? JSON.stringify(value) : value;
  let found = false;
  const next: string[] = [];
  for (const line of lines) {
    if (line.trimStart().startsWith(`${key}=`)) {
      if (!found) {
        next.push(`${key}=${escaped}`);
        found = true;
      }
      continue;
    }
    next.push(line);
  }
  if (!found) next.push(`${key}=${escaped}`);
  return next;
}

export function persistFriendSettings(settings: FriendBotSettings): FriendBotSettings {
  const applied = applyFriendBotSettings(settings);
  const existing = existsSync(envPath) ? readFileSync(envPath, "utf8") : "";
  let lines = existing.length ? existing.split(/\r?\n/) : [];
  if (lines.length === 1 && lines[0] === "") lines = [];

  const values: Record<SettingsKey, string> = {
    TRACKED_WALLETS: applied.trackedWallets.join(","),
    MIN_BUY_SOL: String(applied.minBuySol),
    MAX_BUY_SOL: String(applied.maxBuySol),
    COPY_BUY_PERCENTAGE: String(applied.copyBuyPercentage),
    SOL_RESERVE: String(applied.solReserveSol),
    TIP_LAMPORTS: String(applied.tipLamports),
    PRIORITY_FEE_MICRO_LAMPORTS: String(applied.priorityFeeMicroLamports),
    MIRROR_SLIPPAGE_BPS: String(applied.mirrorSlippageBps),
    MIRROR_SELLS: applied.mirrorSells ? "true" : "false",
    SIMULATION_MODE: applied.simulationMode ? "true" : "false",
    MAX_OPEN_POSITIONS: applied.maxOpenPositions != null ? String(applied.maxOpenPositions) : "",
    MAX_SOL_PER_TOKEN: applied.maxSolPerTokenSol != null ? String(applied.maxSolPerTokenSol) : "",
    MAX_SESSION_DEPLOYED_SOL: applied.maxSessionDeployedSol != null ? String(applied.maxSessionDeployedSol) : "",
    MAX_OPEN_DEPLOYED_SOL: applied.maxOpenDeployedSol != null ? String(applied.maxOpenDeployedSol) : "",
    MAX_BUY_EXECUTION_FEE_SOL: applied.maxBuyExecutionFeeSol != null ? String(applied.maxBuyExecutionFeeSol) : "",
  };

  for (const key of SETTINGS_KEYS) lines = setEnvLine(lines, key, values[key]);
  writeFileSync(envPath, `${lines.join("\n").replace(/\n+$/, "")}\n`);
  return applied;
}

export function persistFriendRiskSettings(body: Record<string, unknown>): { riskUpdated: boolean } {
  const keyMap: Record<string, string> = {
    stopLossPct: "STOP_LOSS_PCT",
    takeProfitPct: "TAKE_PROFIT_PCT",
    minHoldBeforeStopLossMs: "MIN_HOLD_BEFORE_STOP_LOSS_MS",
    hybridExitEnabled: "HYBRID_EXIT_ENABLED",
    hybridExitTpPct: "HYBRID_EXIT_TP_PCT",
    hybridExitTpSellPct: "HYBRID_EXIT_TP_SELL_PCT",
    hybridExitSourceExitRemaining: "HYBRID_EXIT_SOURCE_EXIT_REMAINING",
    profitLadderEnabled: "PROFIT_LADDER_ENABLED",
    profitLadderSteps: "PROFIT_LADDER_STEPS",
  };

  const updates: Array<[string, string]> = [];
  const runtimeUpdates: Record<string, string | boolean> = {};
  for (const [bodyKey, envKey] of Object.entries(keyMap)) {
    const raw = body[bodyKey];
    if (raw == null) continue;
    if (typeof raw === "boolean") {
      updates.push([envKey, raw ? "true" : "false"]);
      runtimeUpdates[bodyKey] = raw;
    } else {
      updates.push([envKey, String(raw).trim()]);
      runtimeUpdates[bodyKey] = String(raw).trim();
    }
  }
  if (updates.length === 0) return { riskUpdated: false };

  const allowed = new Set<string>(RISK_SETTINGS_KEYS);
  const existing = existsSync(envPath) ? readFileSync(envPath, "utf8") : "";
  let lines = existing.length ? existing.split(/\r?\n/) : [];
  if (lines.length === 1 && lines[0] === "") lines = [];
  for (const [key, value] of updates) {
    if (!allowed.has(key)) continue;
    lines = setEnvLine(lines, key, value);
    process.env[key] = value;
  }
  writeFileSync(envPath, `${lines.join("\n").replace(/\n+$/, "")}\n`);
  applyFriendRiskSettings(runtimeUpdates);
  return { riskUpdated: true };
}

export type SpeedTier = "max" | "balanced" | "eco" | "off";

// Same-slot fee tiers for the friend demo. Max/Balanced/Eco keep the hot sniper ON and only
// change the fee level (priority is charged per compute unit, so cheaper tiers also shrink
// COMPUTE_UNIT_LIMIT). Off disables the sniper so buys use the slower gRPC mirror with no
// snipe fee. Approx cost per landed snipe: max ~0.037, balanced ~0.006, eco ~0.004 SOL. These
// keys are read by the Rust sidecar at startup, so persisting a tier requires a restart.
const SPEED_TIERS: Record<SpeedTier, Record<string, string>> = {
  max: {
    RUST_HOT_SNIPER_ENABLED: "true",
    RUST_HOT_SNIPER_PRIMARY: "true",
    RUST_HOT_SNIPER_JITO_TIP_LAMPORTS: "10000000",
    RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS: "1500000",
    RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS: "18000000",
    COMPUTE_UNIT_LIMIT: "1400000",
    MAX_BUY_EXECUTION_FEE_SOL: "0.05",
  },
  balanced: {
    RUST_HOT_SNIPER_ENABLED: "true",
    RUST_HOT_SNIPER_PRIMARY: "true",
    RUST_HOT_SNIPER_JITO_TIP_LAMPORTS: "3000000",
    RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS: "0",
    RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS: "8000000",
    COMPUTE_UNIT_LIMIT: "400000",
    MAX_BUY_EXECUTION_FEE_SOL: "0.008",
  },
  eco: {
    RUST_HOT_SNIPER_ENABLED: "true",
    RUST_HOT_SNIPER_PRIMARY: "true",
    RUST_HOT_SNIPER_JITO_TIP_LAMPORTS: "2000000",
    RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS: "0",
    RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS: "5000000",
    COMPUTE_UNIT_LIMIT: "300000",
    MAX_BUY_EXECUTION_FEE_SOL: "0.005",
  },
  off: {
    RUST_HOT_SNIPER_ENABLED: "false",
    RUST_HOT_SNIPER_PRIMARY: "false",
    RUST_HOT_SNIPER_JITO_TIP_LAMPORTS: "0",
    RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS: "0",
    RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS: "0",
    MAX_BUY_EXECUTION_FEE_SOL: "0",
  },
};

function normalizeSpeedTier(tier: unknown): SpeedTier {
  return tier === "max" || tier === "eco" || tier === "off" ? tier : "balanced";
}

/** Current tier from the hot-sniper state/tip in the environment. */
export function getSpeedTier(): SpeedTier {
  if (String(process.env.RUST_HOT_SNIPER_ENABLED ?? "").trim().toLowerCase() === "false") return "off";
  const tip = Number(process.env.RUST_HOT_SNIPER_JITO_TIP_LAMPORTS ?? 0);
  if (tip >= 10_000_000) return "max";
  if (tip > 0 && tip <= 2_000_000) return "eco";
  return "balanced";
}

/**
 * Persist a same-slot fee tier to the friend .env. The hot sniper stays ON in every tier.
 * These values are consumed by the Rust sidecar at startup, so the caller must restart the
 * process (under pm2) for them to take effect.
 */
export function persistSpeedTier(tier: unknown): { tier: SpeedTier } {
  const key = normalizeSpeedTier(tier);
  const values = SPEED_TIERS[key];
  const existing = existsSync(envPath) ? readFileSync(envPath, "utf8") : "";
  let lines = existing.length ? existing.split(/\r?\n/) : [];
  if (lines.length === 1 && lines[0] === "") lines = [];
  for (const [k, v] of Object.entries(values)) {
    lines = setEnvLine(lines, k, v);
    process.env[k] = v;
  }
  writeFileSync(envPath, `${lines.join("\n").replace(/\n+$/, "")}\n`);
  return { tier: key };
}

function validatePrivateKeyBase58(value: string): void {
  try {
    const decoded = bs58.decode(value);
    Keypair.fromSecretKey(decoded);
  } catch {
    throw new Error("PRIVATE_KEY_BASE58 is not a valid Solana private key");
  }
}

export function persistFriendSecretSettings(body: Record<string, unknown>): { privateKeyUpdated: boolean } {
  const raw = body.privateKeyBase58;
  if (raw == null || String(raw).trim() === "") return { privateKeyUpdated: false };

  const privateKeyBase58 = String(raw).trim();
  validatePrivateKeyBase58(privateKeyBase58);

  const existing = existsSync(envPath) ? readFileSync(envPath, "utf8") : "";
  let lines = existing.length ? existing.split(/\r?\n/) : [];
  if (lines.length === 1 && lines[0] === "") lines = [];
  lines = setEnvLine(lines, "PRIVATE_KEY_BASE58", privateKeyBase58);
  writeFileSync(envPath, `${lines.join("\n").replace(/\n+$/, "")}\n`);
  process.env.PRIVATE_KEY_BASE58 = privateKeyBase58;

  return { privateKeyUpdated: true };
}
