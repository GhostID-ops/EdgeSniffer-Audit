import { createHash, createHmac, randomBytes } from "node:crypto";
import { hostname } from "node:os";

const LICENSE_PREFIX = "FBOT";
const LICENSE_GROUPS = 4;
const LICENSE_GROUP_BYTES = 4;

export type LicenseStatus = "active" | "revoked" | "expired";

export type LicenseValidationOk = {
  ok: true;
  status: "active";
  expiresAt: string | null;
  graceHours: number;
  machineLimit: number;
  machineSalt?: string;
};

export type LicenseValidationFail = {
  ok: false;
  reason: "revoked" | "expired" | "machine_limit" | "not_found" | "invalid_request" | "server_error";
};

export type LicenseValidationResponse = LicenseValidationOk | LicenseValidationFail;

export function normalizeLicenseKey(key: string): string {
  return key.trim().toUpperCase().replace(/\s+/g, "");
}

export function generateLicenseKey(): string {
  const groups: string[] = [];
  for (let i = 0; i < LICENSE_GROUPS; i++) {
    groups.push(randomBytes(LICENSE_GROUP_BYTES).toString("hex").toUpperCase());
  }
  return `${LICENSE_PREFIX}-${groups.join("-")}`;
}

export function hashLicenseKey(key: string, hmacSecret = ""): string {
  const normalized = normalizeLicenseKey(key);
  if (hmacSecret.trim()) {
    return createHmac("sha256", hmacSecret).update(normalized).digest("hex");
  }
  return createHash("sha256").update(normalized).digest("hex");
}

export function shortKeyFingerprint(key: string): string {
  return createHash("sha256").update(normalizeLicenseKey(key)).digest("hex").slice(0, 16);
}

export function hashMachineFingerprint(fingerprint: string, hmacSecret = ""): string {
  if (hmacSecret.trim()) {
    return createHmac("sha256", hmacSecret).update(fingerprint).digest("hex");
  }
  return createHash("sha256").update(fingerprint).digest("hex");
}

export function createMachineFingerprint(salt = "finalbot-license-v1"): string {
  const home = process.env.USERPROFILE ?? process.env.HOME ?? "";
  const raw = [
    salt,
    hostname(),
    process.platform,
    process.arch,
    createHash("sha256").update(home).digest("hex"),
  ].join("|");
  return createHash("sha256").update(raw).digest("hex");
}

export function parseArgs(argv: string[]): Record<string, string | boolean> {
  const out: Record<string, string | boolean> = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i]!;
    if (!arg.startsWith("--")) continue;
    const key = arg.slice(2);
    const next = argv[i + 1];
    if (next == null || next.startsWith("--")) {
      out[key] = true;
      continue;
    }
    out[key] = next;
    i++;
  }
  return out;
}

export function canActivateMachine(
  activeMachineHashes: string[],
  machineHash: string,
  maxMachines: number
): "allowed" | "machine_limit" {
  if (activeMachineHashes.includes(machineHash)) return "allowed";
  return activeMachineHashes.length < Math.max(1, maxMachines) ? "allowed" : "machine_limit";
}
