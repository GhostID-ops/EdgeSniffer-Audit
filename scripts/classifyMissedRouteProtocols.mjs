import "dotenv/config";
import fs from "node:fs";
import path from "node:path";
import { Connection } from "@solana/web3.js";

const PROGRAM_LABELS = new Map([
  ["CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C", "raydium-cpmm"],
  ["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "raydium-legacy-amm-v4"],
  ["CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", "raydium-clmm"],
  ["routeUGWgWzqBWFcrCfv8tritsqukccJPu3q5GPP3xS", "raydium-router"],
  ["LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj", "raydium-launchlab"],
  ["LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "meteora-dlmm"],
  ["dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN", "meteora-dbc"],
  ["whirLbMiicVdio4qvUfM5KAg6CtVYzwdwq6R8kFJDY", "orca-whirlpool"],
  ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "pumpswap"],
  ["6EF8rrecthR5DkB4tjvSYwVE7D5VvFAZHkmZ3QBjWJ5", "pumpfun"],
]);

function arg(name, fallback = "") {
  const prefix = `--${name}=`;
  const found = process.argv.find((value) => value.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

function readJsonl(file) {
  if (!file || !fs.existsSync(file)) return [];
  return fs
    .readFileSync(file, "utf8")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function num(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function countBy(rows, getKey) {
  const counts = new Map();
  for (const row of rows) {
    const key = getKey(row) || "unknown";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function table(entries, total) {
  if (entries.length === 0) return "  none";
  return entries
    .slice(0, 30)
    .map(([key, count]) => `  ${String(count).padStart(4)}  ${pct(count, total).padStart(6)}  ${key}`)
    .join("\n");
}

function pct(n, d) {
  return d > 0 ? `${((n / d) * 100).toFixed(1)}%` : "0.0%";
}

function short(value) {
  return typeof value === "string" && value.length > 10 ? `${value.slice(0, 10)}...` : value ?? "-";
}

function classify(programIds) {
  const labels = [...new Set(programIds.map((id) => PROGRAM_LABELS.get(id)).filter(Boolean))];
  const raydium = labels.find((label) => label.startsWith("raydium-"));
  if (raydium) return raydium;
  return labels[0] ?? "unknown";
}

function resolveAccountKeys(tx) {
  const message = tx?.transaction?.message;
  if (!message) return [];
  const staticKeys = message.staticAccountKeys ?? message.accountKeys ?? [];
  const loaded = tx.meta?.loadedAddresses;
  const writable = loaded?.writable ?? [];
  const readonly = loaded?.readonly ?? [];
  return [...staticKeys, ...writable, ...readonly].map((key) =>
    typeof key === "string" ? key : key.toBase58()
  );
}

function collectProgramIds(tx) {
  const keys = resolveAccountKeys(tx);
  const message = tx?.transaction?.message;
  const ids = new Set();
  const add = (programIdIndex) => {
    if (typeof programIdIndex !== "number") return;
    const id = keys[programIdIndex];
    if (id) ids.add(id);
  };
  for (const ix of message?.compiledInstructions ?? message?.instructions ?? []) add(ix.programIdIndex);
  for (const group of tx?.meta?.innerInstructions ?? []) {
    for (const ix of group.instructions ?? []) add(ix.programIdIndex);
  }
  return [...ids];
}

function missedNonPumpBuys(rows, sinceMs) {
  const groups = new Map();
  for (const row of rows) {
    if (!row.sourceSignature) continue;
    const ts = num(row.tsMs) ?? Date.parse(row.ts ?? "");
    if (sinceMs > 0 && Number.isFinite(ts) && ts < sinceMs) continue;
    const group = groups.get(row.sourceSignature) ?? [];
    group.push(row);
    groups.set(row.sourceSignature, group);
  }

  const missed = [];
  for (const [sourceSignature, group] of groups) {
    const sorted = [...group].sort((a, b) => (num(a.tsMs) ?? 0) - (num(b.tsMs) ?? 0));
    const plan = sorted.find((row) => row.kind === "execution-plan" && row.route === "jupiter-non-pump");
    if (!plan || plan.side !== "buy") continue;
    const broadcast = sorted.find((row) => (row.kind === "broadcast" || row.kind === "stage:broadcast") && row.success === true);
    if (broadcast) continue;
    missed.push({
      sourceSignature,
      ts: plan.ts ?? sorted[0]?.ts,
      wallet: plan.wallet ?? "unknown",
      detector: plan.detector ?? "unknown",
      mint: plan.mint ?? "unknown",
    });
  }
  return missed;
}

const telemetryPath = arg("telemetry", "data/execution-telemetry.jsonl");
const sinceRaw = arg("since", "");
const max = Math.max(1, Number.parseInt(arg("max", "500"), 10));
const delayMs = Math.max(0, Number.parseInt(arg("delay-ms", "80"), 10));
const outPath = arg("out", "");
const sinceMs = sinceRaw ? Date.parse(sinceRaw) : 0;
const rpc =
  process.env.SECURE_RPC_URL ||
  process.env.RPC_URL ||
  (process.env.HELIUS_API_KEY ? `https://mainnet.helius-rpc.com/?api-key=${process.env.HELIUS_API_KEY}` : "");

if (!rpc) {
  console.error("SECURE_RPC_URL, RPC_URL, or HELIUS_API_KEY is required.");
  process.exit(1);
}

const rows = readJsonl(telemetryPath);
const candidates = missedNonPumpBuys(rows, Number.isFinite(sinceMs) ? sinceMs : 0).slice(-max);
const connection = new Connection(rpc, { commitment: "confirmed", disableRetryOnRateLimit: true });
const results = [];

console.log(`Classifying ${candidates.length} missed non-pump buy source txs from ${path.resolve(telemetryPath)}`);

for (let i = 0; i < candidates.length; i += 1) {
  const item = candidates[i];
  let programIds = [];
  let fetchError = "";
  try {
    const tx = await connection.getTransaction(item.sourceSignature, {
      commitment: "confirmed",
      maxSupportedTransactionVersion: 0,
    });
    if (tx) programIds = collectProgramIds(tx);
    else fetchError = "transaction-not-found";
  } catch (err) {
    fetchError = err instanceof Error ? err.message : String(err);
  }
  const protocol = classify(programIds);
  results.push({ ...item, protocol, programIds, fetchError });
  if ((i + 1) % 25 === 0) console.log(`  classified ${i + 1}/${candidates.length}`);
  if (delayMs > 0) await sleep(delayMs);
}

console.log("");
console.log("Offline Missed Route Protocol Classification");
console.log(`Fetched/classified: ${results.length}`);
console.log("");
console.log("By protocol");
console.log(table(countBy(results, (row) => row.protocol), results.length));
console.log("");
console.log("By wallet");
console.log(table(countBy(results, (row) => row.wallet), results.length));
console.log("");
console.log("By detector");
console.log(table(countBy(results, (row) => row.detector), results.length));
console.log("");
console.log("Fetch errors");
console.log(table(countBy(results.filter((row) => row.fetchError), (row) => row.fetchError), Math.max(1, results.length)));
console.log("");
console.log("Latest classified misses");
for (const row of results.slice(-25).reverse()) {
  const labels = row.programIds.map((id) => PROGRAM_LABELS.get(id) ?? id).join(",");
  console.log(
    `  ${row.ts ?? "-"} ${short(row.sourceSignature)} mint=${short(row.mint)} protocol=${row.protocol}` +
      (labels ? ` programs=${labels}` : "") +
      (row.fetchError ? ` error=${row.fetchError}` : "")
  );
}

if (outPath) {
  fs.mkdirSync(path.dirname(outPath), { recursive: true });
  fs.writeFileSync(outPath, `${results.map((row) => JSON.stringify(row)).join("\n")}\n`);
  console.log(`\nWrote ${outPath}`);
}
