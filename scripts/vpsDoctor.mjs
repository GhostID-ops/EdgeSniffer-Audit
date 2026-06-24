import { existsSync, readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import path from "node:path";

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
  "RUST_HOT_SNIPER_ENABLED",
  "RUST_HOT_SNIPER_JITO_TIP_LAMPORTS",
  "RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS",
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
