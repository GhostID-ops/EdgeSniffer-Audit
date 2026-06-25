import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import bs58 from "bs58";
import { Keypair, PublicKey } from "@solana/web3.js";
import {
  applyFriendBotSettings,
  getFriendBotSettings,
  type FriendBotSettings,
} from "./config.js";

const envPath = resolve(process.cwd(), ".env");

const SETTINGS_KEYS = [
  "TRACKED_WALLETS",
  "MIN_BUY_SOL",
  "MAX_BUY_SOL",
  "COPY_BUY_PERCENTAGE",
  "SOL_RESERVE",
  "TIP_LAMPORTS",
  "PRIORITY_FEE_MICRO_LAMPORTS",
  "MIRROR_SLIPPAGE_BPS",
  "SIMULATION_MODE",
] as const;

type SettingsKey = (typeof SETTINGS_KEYS)[number];

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

function boolFromBody(body: Record<string, unknown>, key: string, fallback: boolean): boolean {
  const raw = body[key];
  if (raw == null || raw === "") return fallback;
  if (typeof raw === "boolean") return raw;
  return ["true", "1", "yes", "on"].includes(String(raw).toLowerCase());
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
    simulationMode: boolFromBody(body, "simulationMode", current.simulationMode),
  };

  if (settings.minBuySol <= 0) throw new Error("Minimum buy must be greater than 0 SOL");
  if (settings.maxBuySol < settings.minBuySol) {
    throw new Error("Maximum buy must be greater than or equal to minimum buy");
  }
  if (settings.copyBuyPercentage < 0) throw new Error("Copy buy percentage cannot be negative");
  if (settings.copyBuyPercentage > 100) settings.copyBuyPercentage = 100;
  if (settings.solReserveSol < 0) throw new Error("SOL reserve cannot be negative");
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
    SIMULATION_MODE: applied.simulationMode ? "true" : "false",
  };

  for (const key of SETTINGS_KEYS) lines = setEnvLine(lines, key, values[key]);
  writeFileSync(envPath, `${lines.join("\n").replace(/\n+$/, "")}\n`);
  return applied;
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
