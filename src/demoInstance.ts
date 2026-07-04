/**
 * Launch a fully isolated demo instance for a friend to try the bot.
 *
 *   npm run friend:demo            # -> ./demo-instance on port 3001
 *   npm run friend:demo myfriend 3005
 *
 * The normal web server runs one bot with one wallet from the repo .env. This
 * launcher creates a separate working directory so a friend's key/settings do
 * not touch your main .env, wallet, data, or live process.
 */
import { randomBytes } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { Keypair } from "@solana/web3.js";
import bs58 from "bs58";

const repoRoot = process.cwd();
const demoName = process.argv[2] && !/^\d+$/.test(process.argv[2]) ? process.argv[2] : "demo-instance";
const port = process.env.WEB_PORT || process.argv.find((a, i) => i >= 2 && /^\d+$/.test(a)) || "3001";
const demoDir = resolve(repoRoot, demoName);
const demoEnvPath = join(demoDir, ".env");
const rustSidecarBin = join(repoRoot, "rust-sidecar", "target", "release", process.platform === "win32" ? "pumpfun-sidecar.exe" : "pumpfun-sidecar");

mkdirSync(demoDir, { recursive: true });
mkdirSync(join(demoDir, "data"), { recursive: true });

const STRIP_EXACT = new Set([
  "PRIVATE_KEY_BASE58",
  "WEB_PORT",
  "WEB_HOST",
  "SIMULATION_MODE",
  "SIMULATION_SOL_BALANCE",
  "STARTING_SOL_BALANCE",
  "BOT_CONTROL_API_ENABLED",
  "UI_BASIC_AUTH_USER",
  "UI_BASIC_AUTH_PASSWORD",
]);
const STRIP_PREFIX = ["TELEGRAM"];

function shouldStrip(key: string): boolean {
  if (STRIP_EXACT.has(key)) return true;
  return STRIP_PREFIX.some((prefix) => key.startsWith(prefix));
}

let burnerPub = "";
let uiUser = "";
let uiPassword = "";

function envValue(text: string, key: string): string {
  const line = text.split(/\r?\n/).find((row) => row.trimStart().startsWith(`${key}=`));
  return line ? line.replace(/^\s*[^=]+=/, "").trim().replace(/^"|"$/g, "") : "";
}

function randomPassword(): string {
  return randomBytes(18).toString("base64url");
}

function ensureDemoAuth(): void {
  const existing = existsSync(demoEnvPath) ? readFileSync(demoEnvPath, "utf8") : "";
  uiUser = envValue(existing, "UI_BASIC_AUTH_USER") || "friend";
  uiPassword = envValue(existing, "UI_BASIC_AUTH_PASSWORD") || randomPassword();
  const lines = existing.length ? existing.split(/\r?\n/) : [];
  const next = lines.filter((line) => {
    const trimmed = line.trimStart();
    return !trimmed.startsWith("UI_BASIC_AUTH_USER=") && !trimmed.startsWith("UI_BASIC_AUTH_PASSWORD=");
  });
  next.push(`UI_BASIC_AUTH_USER=${uiUser}`);
  next.push(`UI_BASIC_AUTH_PASSWORD=${uiPassword}`);
  writeFileSync(demoEnvPath, `${next.join("\n").replace(/\n+$/, "")}\n`);
}

function upsertDemoEnv(values: Record<string, string>): void {
  const existing = existsSync(demoEnvPath) ? readFileSync(demoEnvPath, "utf8") : "";
  let lines = existing.length ? existing.split(/\r?\n/) : [];
  for (const [key, value] of Object.entries(values)) {
    let replaced = false;
    lines = lines.map((line) => {
      if (line.trimStart().startsWith(`${key}=`)) {
        replaced = true;
        return `${key}=${value}`;
      }
      return line;
    });
    if (!replaced) lines.push(`${key}=${value}`);
  }
  writeFileSync(demoEnvPath, `${lines.join("\n").replace(/\n+$/, "")}\n`);
}

// Set keys ONLY if absent. Used for values the friend can change from the UI (the same-slot
// fee tier via /api/speed) — forcing them on every launch would clobber his choice.
function defaultDemoEnv(values: Record<string, string>): void {
  const existing = existsSync(demoEnvPath) ? readFileSync(demoEnvPath, "utf8") : "";
  let lines = existing.length ? existing.split(/\r?\n/) : [];
  let changed = false;
  for (const [key, value] of Object.entries(values)) {
    if (!lines.some((line) => line.trimStart().startsWith(`${key}=`))) {
      lines.push(`${key}=${value}`);
      changed = true;
    }
  }
  if (changed) writeFileSync(demoEnvPath, `${lines.join("\n").replace(/\n+$/, "")}\n`);
}

if (!existsSync(demoEnvPath)) {
  const mainEnv = existsSync(join(repoRoot, ".env")) ? readFileSync(join(repoRoot, ".env"), "utf8") : "";
  const kept = mainEnv
    .split(/\r?\n/)
    .filter((line) => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) return false;
      const key = trimmed.split("=")[0]!.trim();
      return key.length > 0 && !shouldStrip(key);
    });

  const burner = Keypair.generate();
  burnerPub = burner.publicKey.toBase58();

  const out = [
    "# ============================================================",
    "# Obsidian Labs - ISOLATED demo instance (auto-generated).",
    "# Safe to share access to. The friend's pasted key is written",
    "# HERE only. Your main wallet/.env is never touched.",
    "# ============================================================",
    ...kept,
    "",
    "# --- demo overrides ---",
    "BOT_CONTROL_API_ENABLED=true",
    "SIMULATION_MODE=true",
    "SIMULATION_SOL_BALANCE=1",
    "STARTING_SOL_BALANCE=1",
    `WEB_PORT=${port}`,
    "WEB_HOST=0.0.0.0",
    "UI_BASIC_AUTH_USER=friend",
    `UI_BASIC_AUTH_PASSWORD=${randomPassword()}`,
    // No imposed per-buy ceiling — the friend controls his own Max Buy + session cap in the UI.
    "FRIEND_MAX_LIVE_BUY_SOL=",
    // Ceiling on TOTAL buy fees (tip + priority + helius) in SOL. Raised to 0.02 so the
    // same-slot cheap tier (~0.006/snipe) is not clamped. Priority is charged per compute
    // unit (price x COMPUTE_UNIT_LIMIT), which is why the tier lowers the CU limit to 400k.
    "MAX_BUY_EXECUTION_FEE_SOL=0.02",
    "FRIEND_MAX_LIVE_TIP_LAMPORTS=5000000",
    "FRIEND_MAX_LIVE_PRIORITY_FEE_MICRO_LAMPORTS=10000000",
    "MIN_BUY_SOL=0.02",
    "MAX_BUY_SOL=0.1",
    "COPY_BUY_PERCENTAGE=10",
    "SOL_RESERVE=0.05",
    "TIP_LAMPORTS=3000000",
    "PRIORITY_FEE_MICRO_LAMPORTS=8000000",
    "MAX_OPEN_POSITIONS=2",
    "MAX_SOL_PER_TOKEN=0.15",
    "MAX_SESSION_DEPLOYED_SOL=0.5",
    "JUPITER_FALLBACK_ENABLED=false",
    "PUMPSWAP_DIRECT_BUY_ENABLED=true",
    "PUMPSWAP_DIRECT_PREFLIGHT=false",
    "WATCHER_POLL_FALLBACK_ENABLED=false",
    "RUST_SIDECAR_ENABLED=true",
    "RUST_SIDECAR_YELLOWSTONE_ENABLED=true",
    "RUST_SIDECAR_USE_CARGO=false",
    `RUST_SIDECAR_BIN=${rustSidecarBin}`,
    "RUST_EXECUTOR_ENABLED=true",
    "RUST_EXECUTOR_TIMEOUT_MS=1500",
    "RUST_ONLY_BUYS=true",
    "RUST_ONLY_SELLS=true",
    "JITO_SHREDSTREAM_ENABLED=true",
    "JITO_SHREDSTREAM_GRPC_URL=http://127.0.0.1:9999",
    "# Same-slot ShredStream hot sniper (cheap tier ~0.006 SOL/snipe) so the demo lands in-slot.",
    "RUST_HOT_SNIPER_ENABLED=true",
    "RUST_HOT_SNIPER_PRIMARY=true",
    "RUST_HOT_SNIPER_PRIMARY_FALLBACK_ENABLED=true",
    "RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS=20",
    "RUST_HOT_SNIPER_JITO_TIP_LAMPORTS=3000000",
    "RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS=0",
    "RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS=8000000",
    "COMPUTE_UNIT_LIMIT=400000",
    "JITO_TIP_ORACLE_ENABLED=true",
    "DYNAMIC_PRIORITY_FEE_ENABLED=true",
    "JITO_CONNECTION_WARMUP_ENABLED=true",
    "BROADCAST_RACE_ENABLED=true",
    "JITO_SEND_MODE=race",
    "EXECUTION_MULTI_SEND_ENABLED=true",
    "SENDER_RPC_FALLBACK_ENABLED=true",
    "# Throwaway burner wallet (0 SOL). Replaced if the friend pastes their own key.",
    `PRIVATE_KEY_BASE58=${bs58.encode(burner.secretKey)}`,
    "",
  ];
  writeFileSync(demoEnvPath, out.join("\n"));
}

ensureDemoAuth();
upsertDemoEnv({
  // Sales demo: the friend is trialing the bot to buy it, so he gets the REAL same-slot
  // sniper (below). No imposed per-buy ceiling — the friend sets his own Max Buy and
  // session cap in the UI (advanced controls). Empty FRIEND_MAX_LIVE_BUY_SOL makes
  // settingsStore skip the clamp entirely, so his UI value is authoritative.
  FRIEND_MAX_LIVE_BUY_SOL: "",
  MAX_BUY_EXECUTION_FEE_SOL: "0.02",
  FRIEND_MAX_LIVE_TIP_LAMPORTS: "5000000",
  FRIEND_MAX_LIVE_PRIORITY_FEE_MICRO_LAMPORTS: "10000000",
  // Fast pump.fun tokens move between quote and fill. 12% was too tight and caused
  // first-attempt buy (6042) / sell (6003) reverts + retries. Match the proven CyA
  // configs (60%). The UI strategy presets override per-strategy on start.
  MIRROR_SLIPPAGE_BPS: "5000",
  SIMULATION_SOL_BALANCE: "1",
  STARTING_SOL_BALANCE: "1",
  JUPITER_FALLBACK_ENABLED: "false",
  PUMPSWAP_DIRECT_BUY_ENABLED: "true",
  PUMPSWAP_DIRECT_PREFLIGHT: "false",
  WATCHER_POLL_FALLBACK_ENABLED: "false",
  RUST_SIDECAR_ENABLED: "true",
  RUST_SIDECAR_YELLOWSTONE_ENABLED: "true",
  RUST_SIDECAR_USE_CARGO: "false",
  RUST_SIDECAR_BIN: rustSidecarBin,
  RUST_EXECUTOR_ENABLED: "true",
  RUST_EXECUTOR_TIMEOUT_MS: "1500",
  RUST_ONLY_BUYS: "true",
  RUST_ONLY_SELLS: "true",
  JITO_SHREDSTREAM_ENABLED: "true",
  JITO_SHREDSTREAM_GRPC_URL: "http://127.0.0.1:9999",
  // Same-slot ShredStream hot sniper, cheap fee tier (~0.006 SOL/snipe). Primary path with
  // a 20ms head-start + Rust backstop. Uses its own RUST_HOT_SNIPER_* fees (not the friend
  // UI caps), so the demo lands in Cented's slot instead of one hop behind. This is what the
  // prospective buyer is here to feel — do not gate it behind the cheap-demo profile.
  RUST_HOT_SNIPER_ENABLED: "true",
  RUST_HOT_SNIPER_PRIMARY: "true",
  RUST_HOT_SNIPER_PRIMARY_FALLBACK_ENABLED: "true",
  RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS: "20",
  // The hot-sniper FEE values + COMPUTE_UNIT_LIMIT are set via defaultDemoEnv below
  // (set-if-missing) so the friend's UI Speed selector (/api/speed) can change them and the
  // choice survives restarts. Only RUST_HOT_SNIPER_ENABLED stays forced so the sniper is
  // always guaranteed on regardless of tier.
  JITO_TIP_ORACLE_ENABLED: "true",
  DYNAMIC_PRIORITY_FEE_ENABLED: "true",
  JITO_CONNECTION_WARMUP_ENABLED: "true",
  BROADCAST_RACE_ENABLED: "true",
  JITO_SEND_MODE: "race",
  EXECUTION_MULTI_SEND_ENABLED: "true",
  SENDER_RPC_FALLBACK_ENABLED: "true",
});

// Same-slot fee defaults (balanced tier ~0.006 SOL/snipe). Set-if-missing so the friend's UI
// Speed selector can switch to Max/Eco and the choice persists across restarts.
defaultDemoEnv({
  RUST_HOT_SNIPER_JITO_TIP_LAMPORTS: "3000000",
  RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS: "0",
  RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS: "8000000",
  COMPUTE_UNIT_LIMIT: "400000",
});

for (const file of ["execution-telemetry.jsonl", "trades.jsonl"]) {
  const src = join(repoRoot, "data", file);
  const dst = join(demoDir, "data", file);
  if (existsSync(src) && !existsSync(dst)) {
    try {
      copyFileSync(src, dst);
    } catch {
      // Non-fatal: the dashboard can run without seeded telemetry.
    }
  }
}

process.chdir(demoDir);
process.env.WEB_PORT = String(port);
process.env.BOT_CONTROL_API_ENABLED = "true";

console.log("Obsidian Labs - ISOLATED demo instance");
console.log("  dir:    " + demoDir);
console.log("  env:    " + demoEnvPath + "  (the friend's key writes here only)");
if (burnerPub) console.log("  burner: " + burnerPub + "  (empty throwaway - your wallet is untouched)");
console.log("  login:  " + uiUser + " / " + uiPassword);
console.log("  open:   http://localhost:" + port + "/friend-simple   (or tunnel local port -> this VPS port)");
console.log("  infra:  Rust sidecar + Jito ShredStream + Rust executor requested.\n");
console.log("  Ctrl+C to stop.\n");

await import("./web.js");
