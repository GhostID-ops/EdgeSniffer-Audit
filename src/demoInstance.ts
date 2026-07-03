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
    "FRIEND_MAX_LIVE_BUY_SOL=0.03",
    // Ceiling on live fees per buy. Must stay above the strategy fee presets in
    // public/friend-simple.html or the UI's auto-applied tip/priority get clamped to
    // this and buys stop landing. 0 here would force every live buy to zero fees.
    "FRIEND_MAX_LIVE_TIP_LAMPORTS=6000000",
    "FRIEND_MAX_LIVE_PRIORITY_FEE_MICRO_LAMPORTS=15000000",
    "MIN_BUY_SOL=0.01",
    "MAX_BUY_SOL=0.02",
    "COPY_BUY_PERCENTAGE=10",
    "SOL_RESERVE=0.05",
    "TIP_LAMPORTS=0",
    "PRIORITY_FEE_MICRO_LAMPORTS=0",
    "MAX_OPEN_POSITIONS=1",
    "MAX_SOL_PER_TOKEN=0.03",
    "MAX_SESSION_DEPLOYED_SOL=0.06",
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
    "# Throwaway burner wallet (0 SOL). Replaced if the friend pastes their own key.",
    `PRIVATE_KEY_BASE58=${bs58.encode(burner.secretKey)}`,
    "",
  ];
  writeFileSync(demoEnvPath, out.join("\n"));
}

ensureDemoAuth();
upsertDemoEnv({
  FRIEND_MAX_LIVE_BUY_SOL: "0.03",
  FRIEND_MAX_LIVE_TIP_LAMPORTS: "6000000",
  FRIEND_MAX_LIVE_PRIORITY_FEE_MICRO_LAMPORTS: "15000000",
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
