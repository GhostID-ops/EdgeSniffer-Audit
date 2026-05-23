import "dotenv/config";
import { readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import {
  createMachineFingerprint,
  normalizeLicenseKey,
  shortKeyFingerprint,
  type LicenseValidationResponse,
} from "./licenseCore.js";

type LicenseCache = {
  keyFingerprint: string;
  machineFingerprint: string;
  status: "active";
  expiresAt: string | null;
  lastValidatedAt: string;
  graceHours: number;
};

const LICENSE_CACHE_FILE = resolve(process.env.LICENSE_CACHE_FILE ?? ".license-cache.json");
const DEFAULT_GRACE_HOURS = Math.max(
  1,
  parseInt(process.env.LICENSE_CACHE_TTL_HOURS ?? "24", 10)
);

export class LicenseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LicenseError";
  }
}

function isTruthy(value: string | undefined): boolean {
  return value === "true" || value === "1" || value === "yes";
}

function appVersion(): string {
  return process.env.npm_package_version ?? "1.0.0";
}

function cacheValid(cache: LicenseCache, key: string, machineFingerprint: string): { ok: true; until: Date } | { ok: false } {
  if (cache.keyFingerprint !== shortKeyFingerprint(key)) return { ok: false };
  if (cache.machineFingerprint !== machineFingerprint) return { ok: false };
  if (cache.status !== "active") return { ok: false };
  if (cache.expiresAt != null && Date.parse(cache.expiresAt) <= Date.now()) return { ok: false };

  const validatedAt = Date.parse(cache.lastValidatedAt);
  if (!Number.isFinite(validatedAt)) return { ok: false };
  const graceMs = Math.max(1, cache.graceHours || DEFAULT_GRACE_HOURS) * 60 * 60 * 1000;
  const until = new Date(validatedAt + graceMs);
  if (until.getTime() <= Date.now()) return { ok: false };
  return { ok: true, until };
}

async function readCache(): Promise<LicenseCache | null> {
  if (!existsSync(LICENSE_CACHE_FILE)) return null;
  try {
    const raw = await readFile(LICENSE_CACHE_FILE, "utf8");
    const parsed = JSON.parse(raw) as LicenseCache;
    if (!parsed || parsed.status !== "active") return null;
    return parsed;
  } catch {
    return null;
  }
}

async function writeCache(cache: LicenseCache): Promise<void> {
  await writeFile(LICENSE_CACHE_FILE, `${JSON.stringify(cache, null, 2)}\n`, "utf8");
}

function reasonText(reason: LicenseValidationResponse extends infer T ? T extends { ok: false; reason: infer R } ? R : never : never): string {
  switch (reason) {
    case "revoked":
      return "license was revoked";
    case "expired":
      return "license expired";
    case "machine_limit":
      return "license is already activated on another machine";
    case "not_found":
      return "license key was not found";
    case "invalid_request":
      return "license validation request was invalid";
    default:
      return "license server error";
  }
}

export async function validateLicense(): Promise<void> {
  if (isTruthy(process.env.LICENSE_DISABLE_CHECKS)) {
    console.warn("License checks disabled by LICENSE_DISABLE_CHECKS=true.");
    return;
  }

  const licenseKey = normalizeLicenseKey(process.env.LICENSE_KEY ?? "");
  const licenseApiUrl = (process.env.LICENSE_API_URL ?? "").trim().replace(/\/+$/, "");
  if (!licenseKey) throw new LicenseError("LICENSE_KEY is not set.");
  if (!licenseApiUrl) throw new LicenseError("LICENSE_API_URL is not set.");

  const machineFingerprint = createMachineFingerprint(process.env.LICENSE_MACHINE_SALT);
  const cache = await readCache();

  try {
    const response = await fetch(`${licenseApiUrl}/validate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        licenseKey,
        machineFingerprint,
        appVersion: appVersion(),
      }),
      signal: AbortSignal.timeout(5_000),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const result = (await response.json()) as LicenseValidationResponse;
    if (!result.ok) {
      throw new LicenseError(`Invalid license: ${reasonText(result.reason)}.`);
    }

    await writeCache({
      keyFingerprint: shortKeyFingerprint(licenseKey),
      machineFingerprint,
      status: "active",
      expiresAt: result.expiresAt,
      lastValidatedAt: new Date().toISOString(),
      graceHours: result.graceHours || DEFAULT_GRACE_HOURS,
    });
    console.log(
      `License valid${result.expiresAt ? ` until ${result.expiresAt}` : ""} (machine limit ${result.machineLimit}).`
    );
    return;
  } catch (err) {
    if (err instanceof LicenseError) throw err;
    if (cache) {
      const valid = cacheValid(cache, licenseKey, machineFingerprint);
      if (valid.ok) {
        console.warn(`License API unavailable; using cached validation until ${valid.until.toISOString()}.`);
        return;
      }
    }
    const msg = err instanceof Error ? err.message : String(err);
    throw new LicenseError(`License validation failed and no fresh cache is available: ${msg}`);
  }
}

export async function validateLicenseOrExit(): Promise<void> {
  try {
    await validateLicense();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`License check failed: ${msg}`);
    process.exit(1);
  }
}
