import "dotenv/config";
import { createServer, IncomingMessage, ServerResponse } from "node:http";
import {
  generateLicenseKey,
  hashLicenseKey,
  hashMachineFingerprint,
  normalizeLicenseKey,
  canActivateMachine,
  type LicenseValidationResponse,
} from "./licenseCore.js";

type LicenseRow = {
  id: string;
  license_key_hash: string;
  label: string | null;
  status: "active" | "revoked" | "expired";
  expires_at: string | null;
  max_machines: number;
  created_at: string;
  revoked_at: string | null;
  notes: string | null;
};

type ActivationRow = {
  id: string;
  license_id: string;
  machine_fingerprint_hash: string;
};

const PORT = Math.max(1, parseInt(process.env.LICENSE_API_PORT ?? "8787", 10));
const HOST = process.env.LICENSE_API_HOST ?? "127.0.0.1";
const SUPABASE_URL = (process.env.SUPABASE_URL ?? "").replace(/\/+$/, "");
const SUPABASE_SERVICE_ROLE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY ?? "";
const ADMIN_TOKEN = process.env.LICENSE_ADMIN_TOKEN ?? "";
const LICENSE_HASH_SECRET = process.env.LICENSE_HASH_SECRET ?? "";
const MACHINE_HASH_SECRET = process.env.LICENSE_MACHINE_HASH_SECRET ?? LICENSE_HASH_SECRET;
const DEFAULT_GRACE_HOURS = Math.max(1, parseInt(process.env.LICENSE_CACHE_TTL_HOURS ?? "24", 10));

function requireServerEnv(): void {
  const missing = [
    ["SUPABASE_URL", SUPABASE_URL],
    ["SUPABASE_SERVICE_ROLE_KEY", SUPABASE_SERVICE_ROLE_KEY],
    ["LICENSE_ADMIN_TOKEN", ADMIN_TOKEN],
  ].filter(([, value]) => !value);
  if (missing.length > 0) {
    throw new Error(`Missing required env: ${missing.map(([k]) => k).join(", ")}`);
  }
}

function headers(extra?: Record<string, string>): Record<string, string> {
  return {
    apikey: SUPABASE_SERVICE_ROLE_KEY,
    authorization: `Bearer ${SUPABASE_SERVICE_ROLE_KEY}`,
    "content-type": "application/json",
    ...(extra ?? {}),
  };
}

async function supabase<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers: headers(init.headers as Record<string, string> | undefined),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`Supabase ${response.status}: ${text}`);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  if (text.length === 0) return undefined as T;
  return JSON.parse(text) as T;
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(text),
  });
  res.end(text);
}

async function readJson(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  if (chunks.length === 0) return {};
  return JSON.parse(Buffer.concat(chunks).toString("utf8")) as Record<string, unknown>;
}

function isAdmin(req: IncomingMessage): boolean {
  const header = req.headers.authorization ?? "";
  return header === `Bearer ${ADMIN_TOKEN}` || req.headers["x-admin-token"] === ADMIN_TOKEN;
}

function isExpired(row: LicenseRow): boolean {
  return row.expires_at != null && Date.parse(row.expires_at) <= Date.now();
}

async function logEvent(
  licenseId: string | null,
  eventType: string,
  machineFingerprintHash: string | null,
  metadata: Record<string, unknown> = {}
): Promise<void> {
  try {
    await supabase("license_events", {
      method: "POST",
      headers: { prefer: "return=minimal" },
      body: JSON.stringify({
        license_id: licenseId,
        event_type: eventType,
        machine_fingerprint_hash: machineFingerprintHash,
        metadata,
      }),
    });
  } catch (err) {
    console.warn("License event log failed:", err instanceof Error ? err.message : String(err));
  }
}

async function findLicenseByKey(key: string): Promise<LicenseRow | null> {
  const keyHash = hashLicenseKey(key, LICENSE_HASH_SECRET);
  const rows = await supabase<LicenseRow[]>(
    `licenses?license_key_hash=eq.${encodeURIComponent(keyHash)}&limit=1`
  );
  return rows[0] ?? null;
}

async function handleValidate(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const body = await readJson(req);
  const licenseKey = typeof body.licenseKey === "string" ? normalizeLicenseKey(body.licenseKey) : "";
  const machineFingerprint = typeof body.machineFingerprint === "string" ? body.machineFingerprint : "";
  const appVersion = typeof body.appVersion === "string" ? body.appVersion : "unknown";
  if (!licenseKey || !machineFingerprint) {
    return sendJson(res, 400, { ok: false, reason: "invalid_request" } satisfies LicenseValidationResponse);
  }

  const machineHash = hashMachineFingerprint(machineFingerprint, MACHINE_HASH_SECRET);
  const license = await findLicenseByKey(licenseKey);
  if (!license) {
    await logEvent(null, "validate_not_found", machineHash, { appVersion });
    return sendJson(res, 200, { ok: false, reason: "not_found" } satisfies LicenseValidationResponse);
  }
  if (license.status === "revoked") {
    await logEvent(license.id, "validate_revoked", machineHash, { appVersion });
    return sendJson(res, 200, { ok: false, reason: "revoked" } satisfies LicenseValidationResponse);
  }
  if (license.status === "expired" || isExpired(license)) {
    await logEvent(license.id, "validate_expired", machineHash, { appVersion });
    return sendJson(res, 200, { ok: false, reason: "expired" } satisfies LicenseValidationResponse);
  }

  const activations = await supabase<ActivationRow[]>(
    `license_activations?license_id=eq.${license.id}`
  );
  const existing = activations.find((a) => a.machine_fingerprint_hash === machineHash);
  if (
    canActivateMachine(
      activations.map((a) => a.machine_fingerprint_hash),
      machineHash,
      license.max_machines
    ) === "machine_limit"
  ) {
    await logEvent(license.id, "validate_machine_limit", machineHash, {
      appVersion,
      activationCount: activations.length,
    });
    return sendJson(res, 200, { ok: false, reason: "machine_limit" } satisfies LicenseValidationResponse);
  }

  if (existing) {
    await supabase(`license_activations?id=eq.${existing.id}`, {
      method: "PATCH",
      headers: { prefer: "return=minimal" },
      body: JSON.stringify({ last_seen_at: new Date().toISOString(), last_ip: req.socket.remoteAddress ?? null }),
    });
  } else {
    await supabase("license_activations", {
      method: "POST",
      headers: { prefer: "return=minimal" },
      body: JSON.stringify({
        license_id: license.id,
        machine_fingerprint_hash: machineHash,
        machine_label: null,
        last_ip: req.socket.remoteAddress ?? null,
      }),
    });
  }
  await logEvent(license.id, "validate_ok", machineHash, { appVersion });
  return sendJson(res, 200, {
    ok: true,
    status: "active",
    expiresAt: license.expires_at,
    graceHours: DEFAULT_GRACE_HOURS,
    machineLimit: license.max_machines,
  } satisfies LicenseValidationResponse);
}

async function handleCreateLicense(req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (!isAdmin(req)) return sendJson(res, 401, { error: "unauthorized" });
  const body = await readJson(req);
  const key = generateLicenseKey();
  const label = typeof body.label === "string" ? body.label : null;
  const notes = typeof body.notes === "string" ? body.notes : null;
  const expiresAt = typeof body.expiresAt === "string" && body.expiresAt.trim() ? body.expiresAt : null;
  const maxMachines =
    typeof body.maxMachines === "number" && Number.isInteger(body.maxMachines) && body.maxMachines > 0
      ? body.maxMachines
      : 1;
  const inserted = await supabase<LicenseRow[]>("licenses", {
    method: "POST",
    headers: { prefer: "return=representation" },
    body: JSON.stringify({
      license_key_hash: hashLicenseKey(key, LICENSE_HASH_SECRET),
      label,
      notes,
      expires_at: expiresAt,
      max_machines: maxMachines,
    }),
  });
  await logEvent(inserted[0]?.id ?? null, "admin_create", null, { label, expiresAt, maxMachines });
  return sendJson(res, 201, { license: inserted[0], licenseKey: key });
}

async function handleRevokeLicense(req: IncomingMessage, res: ServerResponse, id: string): Promise<void> {
  if (!isAdmin(req)) return sendJson(res, 401, { error: "unauthorized" });
  const updated = await supabase<LicenseRow[]>(`licenses?id=eq.${encodeURIComponent(id)}`, {
    method: "PATCH",
    headers: { prefer: "return=representation" },
    body: JSON.stringify({ status: "revoked", revoked_at: new Date().toISOString() }),
  });
  await logEvent(id, "admin_revoke", null);
  return sendJson(res, 200, { license: updated[0] ?? null });
}

async function handleResetMachine(req: IncomingMessage, res: ServerResponse, id: string): Promise<void> {
  if (!isAdmin(req)) return sendJson(res, 401, { error: "unauthorized" });
  await supabase(`license_activations?license_id=eq.${encodeURIComponent(id)}`, {
    method: "DELETE",
    headers: { prefer: "return=minimal" },
  });
  await logEvent(id, "admin_reset_machine", null);
  return sendJson(res, 200, { ok: true });
}

async function handleListLicenses(req: IncomingMessage, res: ServerResponse): Promise<void> {
  if (!isAdmin(req)) return sendJson(res, 401, { error: "unauthorized" });
  const rows = await supabase<LicenseRow[]>("licenses?select=*&order=created_at.desc&limit=100");
  return sendJson(res, 200, { licenses: rows });
}

async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
  const url = new URL(req.url ?? "/", "http://localhost");
  if (req.method === "GET" && url.pathname === "/health") return sendJson(res, 200, { ok: true });
  if (req.method === "POST" && url.pathname === "/validate") return handleValidate(req, res);
  if (req.method === "POST" && url.pathname === "/admin/licenses") return handleCreateLicense(req, res);
  if (req.method === "GET" && url.pathname === "/admin/licenses") return handleListLicenses(req, res);

  const revoke = url.pathname.match(/^\/admin\/licenses\/([^/]+)\/revoke$/);
  if (req.method === "POST" && revoke) return handleRevokeLicense(req, res, decodeURIComponent(revoke[1]!));
  const reset = url.pathname.match(/^\/admin\/licenses\/([^/]+)\/reset-machine$/);
  if (req.method === "POST" && reset) return handleResetMachine(req, res, decodeURIComponent(reset[1]!));

  return sendJson(res, 404, { error: "not_found" });
}

export function startLicenseApi(): void {
  requireServerEnv();
  const server = createServer((req, res) => {
    handle(req, res).catch((err) => {
      console.error("License API error:", err);
      sendJson(res, 500, { ok: false, reason: "server_error" });
    });
  });
  server.listen(PORT, HOST, () => {
    console.log(`License API: http://${HOST}:${PORT}`);
  });
}

startLicenseApi();
