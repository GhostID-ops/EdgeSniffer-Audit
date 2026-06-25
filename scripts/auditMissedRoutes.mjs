import fs from "node:fs";
import path from "node:path";

const RAYDIUM_PROGRAMS = new Map([
  ["CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C", "raydium-cpmm"],
  ["675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8", "raydium-legacy-amm-v4"],
  ["CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK", "raydium-clmm"],
  ["routeUGWgWzqBWFcrCfv8tritsqukccJPu3q5GPP3xS", "raydium-router"],
  ["LanMV9sAd7wArD4vJFi2qDdfnVhFxYSUg6eADduJ3uj", "raydium-launchlab"],
]);

const KNOWN_PROGRAMS = new Map([
  ...RAYDIUM_PROGRAMS,
  ["whirLbMiicVdio4qvUfM5KAg6CtVYzwdwq6R8kFJDY", "orca-whirlpool"],
  ["LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo", "meteora-dlmm"],
  ["dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN", "meteora-dbc"],
  ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA", "pumpswap"],
  ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", "pumpfun"],
  ["FLASHX8DrLbgeR8FcfNV1F5krxYcYMUdBkrP1EPBtxB9", "flashx-pumpfun-router"],
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

function countBy(rows, getKey) {
  const counts = new Map();
  for (const row of rows) {
    const key = getKey(row) || "unknown";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function firstStringArray(value) {
  if (!Array.isArray(value)) return [];
  return value.filter((item) => typeof item === "string" && item.length > 0);
}

function classifyPrograms(programIds) {
  const labels = [];
  for (const id of programIds) {
    const label = KNOWN_PROGRAMS.get(id);
    if (label) labels.push(label);
  }
  if (labels.length === 0) return "unknown";
  const raydium = labels.find((label) => label.startsWith("raydium-"));
  const meteora = labels.find((label) => label.startsWith("meteora-"));
  const orca = labels.find((label) => label.startsWith("orca-"));
  if (raydium) return raydium;
  if (meteora) return meteora;
  if (orca) return orca;
  if (labels.includes("pumpswap")) return "pumpswap";
  if (labels.includes("pumpfun")) return "pumpfun";
  return labels[0];
}

function short(value) {
  return typeof value === "string" && value.length > 10 ? `${value.slice(0, 10)}...` : value ?? "-";
}

function fmtPct(n, d) {
  return d > 0 ? `${((n / d) * 100).toFixed(1)}%` : "0.0%";
}

function table(entries, total) {
  if (entries.length === 0) return "  none";
  return entries
    .slice(0, 20)
    .map(([key, count]) => `  ${String(count).padStart(4)}  ${fmtPct(count, total).padStart(6)}  ${key}`)
    .join("\n");
}

const telemetryPath = arg("telemetry", "data/execution-telemetry.jsonl");
const sinceRaw = arg("since", "");
const limit = Number.parseInt(arg("limit", "25"), 10);
const sinceMs = sinceRaw ? Date.parse(sinceRaw) : 0;
const rows = readJsonl(telemetryPath).filter((row) => {
  const ts = num(row.tsMs) ?? Date.parse(row.ts ?? "");
  return !Number.isFinite(sinceMs) || sinceMs <= 0 || ts >= sinceMs;
});

const groups = new Map();
for (const row of rows) {
  if (!row.sourceSignature) continue;
  const group = groups.get(row.sourceSignature) ?? [];
  group.push(row);
  groups.set(row.sourceSignature, group);
}

const missed = [];
for (const [sourceSignature, group] of groups) {
  const sorted = [...group].sort((a, b) => (num(a.tsMs) ?? 0) - (num(b.tsMs) ?? 0));
  const plan = sorted.find((row) => row.kind === "execution-plan" && row.route === "jupiter-non-pump");
  if (!plan || plan.side !== "buy") continue;
  const skip = sorted.find(
    (row) =>
      row.kind === "mirror-skip" &&
      row.side === "buy" &&
      String(row.errorSummary ?? "").includes("rust-only no accepted buy path")
  );
  const broadcast = sorted.find((row) => (row.kind === "broadcast" || row.kind === "stage:broadcast") && row.success === true);
  if (broadcast) continue;
  const source = sorted.find((row) => row.kind === "source-detected");
  const programIds = [
    ...firstStringArray(plan.details?.sourceProgramIds),
    ...firstStringArray(source?.details?.sourceProgramIds),
    ...firstStringArray(skip?.details?.sourceProgramIds),
  ];
  missed.push({
    sourceSignature,
    ts: plan.ts ?? source?.ts ?? sorted[0]?.ts,
    wallet: plan.wallet ?? source?.wallet ?? "unknown",
    detector: plan.detector ?? source?.detector ?? source?.details?.detector ?? "unknown",
    mint: plan.mint ?? source?.mint ?? "unknown",
    route: plan.route,
    reason: plan.details?.reason ?? "non-pump mint without local protocol evidence",
    protocol: classifyPrograms(programIds),
    programIds: [...new Set(programIds)],
    skipReason: skip?.errorSummary ?? "",
  });
}

console.log("Missed Route Audit");
console.log(`File: ${path.resolve(telemetryPath)}`);
console.log(`Rows: ${rows.length}`);
console.log(`Missed non-pump buy signals: ${missed.length}`);
console.log("");
console.log("By inferred protocol");
console.log(table(countBy(missed, (row) => row.protocol), missed.length));
console.log("");
console.log("By detector");
console.log(table(countBy(missed, (row) => row.detector), missed.length));
console.log("");
console.log("By wallet");
console.log(table(countBy(missed, (row) => row.wallet), missed.length));
console.log("");
console.log("By reason");
console.log(table(countBy(missed, (row) => String(row.reason)), missed.length));
console.log("");
console.log(`Latest ${Math.max(0, limit)} misses`);
for (const row of missed.slice(-Math.max(0, limit)).reverse()) {
  const programs = row.programIds.length ? ` programs=${row.programIds.join(",")}` : "";
  console.log(
    `  ${row.ts ?? "-"} ${short(row.sourceSignature)} mint=${short(row.mint)} ` +
      `protocol=${row.protocol} detector=${row.detector}${programs}`
  );
}
