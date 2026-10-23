import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);

const root = process.cwd();
const envPath = path.join(root, ".env");

const WATCH_KEYS = [
  "RUST_ONLY_BUYS",
  "RUST_ONLY_SELLS",
  "JUPITER_FALLBACK_ENABLED",
  "BROADCAST_RACE_ENABLED",
  "EXECUTION_MULTI_SEND_ENABLED",
  "USE_JITO_DIRECT",
  "USE_JITO_FALLBACK",
  "JITO_SEND_MODE",
  "SENDER_RPC_FALLBACK_ENABLED",
  "TIP_LAMPORTS",
  "PRIORITY_FEE_MICRO_LAMPORTS",
  "DYNAMIC_PRIORITY_FEE_ENABLED",
  "DYNAMIC_PRIORITY_FEE_MAX_MICROLAMPORTS",
  "EXECUTION_TIP_MULTIPLIERS",
  "EXECUTION_PRIORITY_FEE_MULTIPLIERS",
  "SHYFT_GRPC_ENABLED",
  "SHYFT_GRPC_URL",
  "SHYFT_RABBITSTREAM_ENABLED",
  "SHYFT_RABBITSTREAM_URL",
  "JITO_SHREDSTREAM_ENABLED",
  "JITO_SHREDSTREAM_GRPC_URL",
  "JITO_CONNECTION_WARMUP_ENABLED",
  "JITO_TIP_ORACLE_ENABLED",
  "JITO_TIP_ORACLE_PERCENTILE",
  "RUST_HOT_SNIPER_ENABLED",
  "RUST_HOT_SNIPER_PRIMARY",
  "RUST_HOT_SNIPER_PRIMARY_FALLBACK_ENABLED",
  "RUST_HOT_SNIPER_PRIMARY_FALLBACK_DELAY_MS",
  "RUST_HOT_SNIPER_BLOCKHASH_REFRESH_MS",
  "RUST_HOT_SNIPER_JITO_TIP_LAMPORTS",
  "RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS",
  "RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS",
  "BLOCK_PUMPFUN_MAYHEM",
];

function parseEnv(text) {
  const values = new Map();
  const counts = new Map();
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq <= 0) continue;
    const key = line.slice(0, eq).trim();
    const value = line.slice(eq + 1);
    values.set(key, value);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return { values, counts };
}

function safeExec(command, args) {
  try {
    return execFileSync(command, args, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
  } catch {
    return "";
  }
}

if (!existsSync(envPath)) {
  console.log("FAIL .env missing in current directory");
  process.exitCode = 1;
} else {
  const { values, counts } = parseEnv(readFileSync(envPath, "utf8"));
  const duplicates = [...counts.entries()].filter(([, count]) => count > 1);

  console.log("FinalBot VPS doctor");
  console.log(`cwd=${root}`);
  console.log(`env=${envPath}`);
  console.log("");
  console.log(duplicates.length === 0 ? "env duplicates: none" : "env duplicates:");
  for (const [key, count] of duplicates) console.log(`  ${key} x${count}`);
  console.log("");
  console.log("watched env:");
  for (const key of WATCH_KEYS) console.log(`  ${key}=${values.get(key) ?? ""}`);
}

const pm2 = safeExec("pm2", ["jlist"]);
console.log("");
if (!pm2) {
  console.log("pm2: not installed or not available in PATH");
} else {
  try {
    const apps = JSON.parse(pm2);
    const names = apps.map((app) => `${app.name}:${app.pm2_env?.status ?? "unknown"}`);
    console.log(`pm2: ${names.length ? names.join(", ") : "no processes"}`);
  } catch {
    console.log("pm2: installed, but status output was not parseable");
  }
}

// ---------------------------------------------------------------------------
// Live-execution audit: every running process capable of live Solana copytrade
// execution, with its cwd, command, env file, resolved wallet, tracked wallets,
// buy sizing and live/rust/jupiter flags. Requires Linux /proc (the VPS); on other
// platforms the env audit above still runs and this section is skipped.
// ---------------------------------------------------------------------------

// Command signatures that can place live Solana orders. dist/index.js is intentionally
// excluded: on this host that is the /opt/pmarke Polymarket paper bot, not copytrading.
const SOLANA_EXEC_PATTERNS = [
  /src\/copytrade\.ts/,
  /src\/botService\.ts/,
  /src\/demoInstance\.ts/,
  /src\/friendWeb\.ts/,
  /src\/web\.ts/,
  /src\/server\.ts/,
  /src\/commands\/launchPaperTrader\.ts/,
  /npm run (bot|start|dev|friend|friend:demo|paper|paper:launch|ui:live|web)\b/,
];

function readlinkSafe(p) {
  try {
    return execFileSync("readlink", ["-f", p], { encoding: "utf8" }).trim();
  } catch {
    return "";
  }
}

function readProcEnvValue(pid, key) {
  try {
    const raw = readFileSync(`/proc/${pid}/environ`, "utf8");
    for (const pair of raw.split("\0")) {
      const eq = pair.indexOf("=");
      if (eq > 0 && pair.slice(0, eq) === key) return pair.slice(eq + 1);
    }
  } catch {
    /* not permitted or gone */
  }
  return "";
}

function isTruthy(v) {
  const s = (v ?? "").trim().toLowerCase();
  return s === "true" || s === "1" || s === "yes" || s === "on";
}

function derivePubkey(privateKeyBase58) {
  // Public key is not secret; the private key is read only in-memory and never printed.
  if (!privateKeyBase58) return "";
  try {
    const bs58 = require("bs58");
    const web3 = require("@solana/web3.js");
    const decode = bs58.default ? bs58.default.decode : bs58.decode;
    return web3.Keypair.fromSecretKey(decode(privateKeyBase58.trim())).publicKey.toBase58();
  } catch {
    return "";
  }
}

function classify(cwd, cmd) {
  if (/launchPaperTrader/.test(cmd) || /EdgeSniffer/.test(cwd)) return "EdgeSniffer dust lane";
  if (existsSync(path.join(cwd, "src", "copytrade.ts"))) return "FinalBot copytrade";
  if (/pmarke/.test(cwd)) return "pmarke (non-Solana)";
  return "unknown";
}

console.log("");
console.log("=== LIVE EXECUTION AUDIT ===");

if (!existsSync("/proc")) {
  console.log("(process scan requires Linux /proc — skipped on this platform)");
} else {
  const psOut = safeExec("ps", ["-eo", "pid=,ppid=,args="]);
  const rows = psOut
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const m = l.match(/^(\d+)\s+(\d+)\s+(.*)$/);
      return m ? { pid: m[1], ppid: m[2], cmd: m[3] } : null;
    })
    .filter(Boolean)
    .filter((r) => !/vpsDoctor\.mjs/.test(r.cmd))
    .filter((r) => SOLANA_EXEC_PATTERNS.some((re) => re.test(r.cmd)));

  if (rows.length === 0) {
    console.log("no running Solana-capable execution processes found.");
  }

  let liveCount = 0;
  for (const r of rows) {
    const cwd = readlinkSafe(`/proc/${r.pid}/cwd`);
    if (!cwd) continue; // parent wrappers (npm/sh) without a resolvable cwd; child covers it
    const dotenvOverride = readProcEnvValue(r.pid, "DOTENV_CONFIG_PATH");
    const envFile = dotenvOverride
      ? path.resolve(dotenvOverride)
      : path.join(cwd, ".env");
    const kind = classify(cwd, r.cmd);

    let env = { values: new Map() };
    if (existsSync(envFile)) env = parseEnv(readFileSync(envFile, "utf8"));
    const v = (k) => env.values.get(k) ?? "";

    const shadow = (v("SHADOW_EXECUTION_MODE") || "true").toLowerCase();
    const sim = (v("SIMULATION_MODE") || v("DRY_RUN") || "").toLowerCase();
    const live =
      shadow === "false" || shadow === "0" || shadow === "no"
        ? !(sim === "true" || sim === "1" || sim === "yes")
        : false;
    if (live) liveCount += 1;

    const pubkey = derivePubkey(v("PRIVATE_KEY_BASE58"));
    const tracked = (v("TRACKED_WALLETS") || "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);

    console.log("");
    console.log(`PID ${r.pid} (ppid ${r.ppid})  [${kind}]  ${live ? "*** LIVE ***" : "shadow/paper"}`);
    console.log(`  cmd:        ${r.cmd.slice(0, 160)}`);
    console.log(`  cwd:        ${cwd}`);
    console.log(`  env file:   ${envFile}${existsSync(envFile) ? "" : "  (missing)"}`);
    console.log(`  wallet:     ${pubkey || "(configured; address hidden)"}`);
    console.log(`  tracked:    ${tracked.length} wallet(s)${tracked.length ? " — " + tracked.map((w) => w.slice(0, 4) + ".." + w.slice(-4)).join(", ") : ""}`);
    console.log(`  buy sizing: MIN_BUY_SOL=${v("MIN_BUY_SOL") || "(default)"} MAX_BUY_SOL=${v("MAX_BUY_SOL") || "(default)"} COPY_BUY_%=${v("COPY_BUY_PERCENTAGE") || "(default)"} behavior=${v("MIN_BUY_BEHAVIOR") || "(default)"}`);
    console.log(`  live exec:  ${live ? "ENABLED" : "no"} (SHADOW_EXECUTION_MODE=${v("SHADOW_EXECUTION_MODE") || "(unset→shadow)"})`);
    console.log(`  jupiter:    ${isTruthy(v("JUPITER_FALLBACK_ENABLED") || "false") ? "FALLBACK ENABLED" : "fallback disabled"}`);
    console.log(`  rust-only:  buys=${isTruthy(v("RUST_ONLY_BUYS"))} sells=${isTruthy(v("RUST_ONLY_SELLS"))}`);
    console.log(`  confirmed:  CONFIRM_COPYTRADE_LIVE=${v("CONFIRM_COPYTRADE_LIVE") || "(unset)"} session=${v("LIVE_SESSION_ID") || v("LIVE_PROFILE_ID") || "(none)"}`);
  }

  console.log("");
  console.log(`summary: ${rows.length} execution-capable process(es), ${liveCount} in LIVE mode.`);

  // Latent boot risk: units that would auto-start copytrading on reboot.
  const enabled = safeExec("systemctl", ["list-unit-files", "--type=service", "--state=enabled", "--no-legend"]);
  const finalbotEnabled = enabled
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => /^finalbot-/.test(l))
    .map((l) => l.split(/\s+/)[0]);
  console.log("");
  if (finalbotEnabled.length) {
    console.log("LATENT BOOT RISK — finalbot units enabled (would start on reboot):");
    for (const u of finalbotEnabled) console.log(`  ${u}  (disable with: systemctl disable ${u})`);
  } else {
    console.log("boot risk: no finalbot-* units enabled.");
  }
}
