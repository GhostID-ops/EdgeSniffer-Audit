import { PublicKey } from "@solana/web3.js";
import { readConnection } from "./config.js";

const TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb";
const MAYHEM_SUPPLY_RAW = 2_000_000_000_000_000;
const FETCH_TIMEOUT_MS = 2500;
const cache = new Map<string, boolean | null>();
const backgroundChecks = new Set<string>();

function isPumpfunMint(mint: string): boolean {
  return mint.endsWith("pump");
}

function objectMentionsMayhem(value: unknown): boolean {
  if (value == null) return false;
  if (typeof value === "string") return value.toLowerCase().includes("mayhem");
  if (typeof value === "boolean" || typeof value === "number") return false;
  if (Array.isArray(value)) return value.some(objectMentionsMayhem);
  if (typeof value === "object") {
    return Object.entries(value).some(([key, nested]) => {
      const keyMentionsMayhem = key.toLowerCase().includes("mayhem");
      const nestedLooksEnabled =
        nested === true ||
        (typeof nested === "number" && nested !== 0) ||
        (typeof nested === "string" && nested.toLowerCase() !== "false");
      return (keyMentionsMayhem && nestedLooksEnabled) || objectMentionsMayhem(nested);
    });
  }
  return false;
}

async function fetchJson(url: string): Promise<unknown | null> {
  try {
    const response = await fetch(url, {
      headers: { Accept: "application/json", Origin: "https://pump.fun" },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

async function fetchText(url: string): Promise<string | null> {
  try {
    const response = await fetch(url, {
      headers: { Accept: "text/html" },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
    if (!response.ok) return null;
    return await response.text();
  } catch {
    return null;
  }
}

async function detectFromPumpApis(mint: string): Promise<boolean | null> {
  const apiCandidates = [
    `https://frontend-api-v3.pump.fun/coins/${mint}?sync=true`,
    `https://pumpportal.fun/api/token/${mint}`,
  ];

  for (const url of apiCandidates) {
    const json = await fetchJson(url);
    if (json && objectMentionsMayhem(json)) return true;
  }

  const html = await fetchText(`https://pump.fun/coin/${mint}`);
  if (html) {
    const lower = html.toLowerCase();
    if (lower.includes("mayhem mode enabled") || lower.includes("mayhem mode completed")) {
      return true;
    }
    if (lower.includes("mayhem mode")) return true;
  }

  return null;
}

async function detectFromMintSupply(mint: string): Promise<boolean | null> {
  try {
    const info = await readConnection.getParsedAccountInfo(new PublicKey(mint));
    const account = info.value;
    if (!account) return null;
    if (account.owner.toBase58() !== TOKEN_2022_PROGRAM_ID) return false;
    const parsed = account.data as
      | { parsed?: { info?: { supply?: string } } }
      | undefined;
    const supplyRaw = Number(parsed?.parsed?.info?.supply ?? "0");
    if (!Number.isFinite(supplyRaw) || supplyRaw <= 0) return null;
    return supplyRaw >= MAYHEM_SUPPLY_RAW * 0.95;
  } catch {
    return null;
  }
}

function scheduleBackgroundPumpApiCheck(mint: string): void {
  if (backgroundChecks.has(mint)) return;
  backgroundChecks.add(mint);

  void detectFromPumpApis(mint)
    .then((apiResult) => {
      if (apiResult === true) {
        cache.set(mint, true);
      } else if (!cache.has(mint)) {
        cache.set(mint, apiResult);
      }
    })
    .catch(() => {
      if (!cache.has(mint)) cache.set(mint, null);
    })
    .finally(() => {
      backgroundChecks.delete(mint);
    });
}

export async function isPumpfunMayhemToken(mint: string): Promise<boolean | null> {
  if (!isPumpfunMint(mint)) return false;
  if (cache.has(mint)) return cache.get(mint)!;

  const supplyResult = await detectFromMintSupply(mint);
  if (supplyResult === true) {
    cache.set(mint, true);
    return true;
  }

  if (supplyResult === false) {
    cache.set(mint, false);
    scheduleBackgroundPumpApiCheck(mint);
    return false;
  }

  scheduleBackgroundPumpApiCheck(mint);
  return null;
}
