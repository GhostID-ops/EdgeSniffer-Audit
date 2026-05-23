import "dotenv/config";
import { hashLicenseKey, normalizeLicenseKey, parseArgs } from "./licenseCore.js";

const API_URL = (process.env.LICENSE_API_URL ?? "").trim().replace(/\/+$/, "");
const ADMIN_TOKEN = process.env.LICENSE_ADMIN_TOKEN ?? "";

function requireAdminEnv(): void {
  if (!API_URL) throw new Error("LICENSE_API_URL is not set.");
  if (!ADMIN_TOKEN) throw new Error("LICENSE_ADMIN_TOKEN is not set.");
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  requireAdminEnv();
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      authorization: `Bearer ${ADMIN_TOKEN}`,
      "content-type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(`License API ${response.status}: ${text}`);
  }
  return (await response.json()) as T;
}

function printUsage(): void {
  console.log(`Usage:
  npm run license:create -- --label "Friend Name" [--expires 2026-06-20] [--max-machines 1] [--notes "..."]
  npm run license:revoke -- --id <license_uuid>
  npm run license:revoke -- --key FBOT-...
  npm run license:reset-machine -- --id <license_uuid>
  npm run license:list
`);
}

async function createLicense(args: Record<string, string | boolean>): Promise<void> {
  const label = typeof args.label === "string" ? args.label : "";
  let expiresAt: string | null = null;
  if (typeof args.expires === "string") {
    const expires = new Date(args.expires);
    if (!Number.isFinite(expires.getTime())) throw new Error(`Invalid --expires date: ${args.expires}`);
    expiresAt = expires.toISOString();
  }
  const maxMachines = typeof args["max-machines"] === "string" ? parseInt(args["max-machines"], 10) : 1;
  const notes = typeof args.notes === "string" ? args.notes : null;
  const result = await api<{ licenseKey: string; license: { id: string; label: string | null; expires_at: string | null; max_machines: number } }>(
    "/admin/licenses",
    {
      method: "POST",
      body: JSON.stringify({ label, expiresAt, maxMachines, notes }),
    }
  );
  console.log("License created:");
  console.log(`  ID: ${result.license.id}`);
  console.log(`  Label: ${result.license.label ?? ""}`);
  console.log(`  Expires: ${result.license.expires_at ?? "never"}`);
  console.log(`  Machines: ${result.license.max_machines}`);
  console.log(`  Key: ${result.licenseKey}`);
  console.log("Store this key now; the server only stores its hash.");
}

async function revokeLicense(args: Record<string, string | boolean>): Promise<void> {
  let id = typeof args.id === "string" ? args.id : "";
  if (!id && typeof args.key === "string") {
    const keyHash = hashLicenseKey(normalizeLicenseKey(args.key), process.env.LICENSE_HASH_SECRET ?? "");
    const list = await api<{ licenses: Array<{ id: string; license_key_hash: string; label: string | null }> }>("/admin/licenses");
    const match = list.licenses.find((l) => l.license_key_hash === keyHash);
    id = match?.id ?? "";
  }
  if (!id) throw new Error("Pass --id <license_uuid> or --key FBOT-...");
  const result = await api<{ license: { id: string; status: string } | null }>(
    `/admin/licenses/${encodeURIComponent(id)}/revoke`,
    { method: "POST", body: "{}" }
  );
  console.log(`Revoked license ${result.license?.id ?? id}.`);
}

async function resetMachine(args: Record<string, string | boolean>): Promise<void> {
  const id = typeof args.id === "string" ? args.id : "";
  if (!id) throw new Error("Pass --id <license_uuid>.");
  await api(`/admin/licenses/${encodeURIComponent(id)}/reset-machine`, { method: "POST", body: "{}" });
  console.log(`Reset machine activations for ${id}.`);
}

async function listLicenses(): Promise<void> {
  const result = await api<{ licenses: Array<{ id: string; label: string | null; status: string; expires_at: string | null; max_machines: number; created_at: string }> }>(
    "/admin/licenses"
  );
  for (const license of result.licenses) {
    console.log(
      `${license.id} | ${license.status} | machines=${license.max_machines} | expires=${license.expires_at ?? "never"} | ${license.label ?? ""}`
    );
  }
}

async function main(): Promise<void> {
  const command = process.argv[2] ?? "";
  const args = parseArgs(process.argv.slice(3));
  if (command === "create") return createLicense(args);
  if (command === "revoke") return revokeLicense(args);
  if (command === "reset-machine") return resetMachine(args);
  if (command === "list") return listLicenses();
  printUsage();
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
