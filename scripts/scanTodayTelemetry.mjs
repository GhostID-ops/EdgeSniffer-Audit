import fs from "node:fs";
import path from "node:path";

const SOL_MINT = "So11111111111111111111111111111111111111112";

function arg(name, fallback = "") {
  const prefix = `--${name}=`;
  const found = process.argv.find((value) => value.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

function readJsonl(file) {
  if (!file || !fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8")
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

function detailNum(row, key) {
  return num(row?.details?.[key]);
}

function quantile(values, q) {
  const xs = values.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (xs.length === 0) return null;
  const idx = Math.min(xs.length - 1, Math.max(0, Math.floor((xs.length - 1) * q)));
  return xs[idx];
}

function fmtMs(value) {
  return value == null ? "-" : `${Math.round(value)}ms`;
}

function fmtNum(value, digits = 2) {
  return value == null || !Number.isFinite(value) ? "-" : value.toFixed(digits);
}

function fmtSol(lamports) {
  return `${fmtNum((lamports ?? 0) / 1_000_000_000, 4)} SOL`;
}

function fmtPct(n, d) {
  return d > 0 ? `${((n / d) * 100).toFixed(1)}%` : "0.0%";
}

function short(value) {
  return typeof value === "string" && value.length > 8 ? `${value.slice(0, 8)}...` : value ?? "-";
}

function tokenMintForTrade(row) {
  if (row.side === "buy") return row.inputMint === SOL_MINT ? row.outputMint : row.inputMint;
  if (row.side === "sell") return row.inputMint === SOL_MINT ? row.outputMint : row.inputMint;
  return row.outputMint ?? row.inputMint ?? "unknown";
}

function bucketCounts(values) {
  const counts = {
    sameSlot: 0,
    nextSlot: 0,
    twoToThree: 0,
    late: 0,
    unknown: 0,
  };
  for (const value of values) {
    if (value == null || !Number.isFinite(value)) counts.unknown += 1;
    else if (value <= 0) counts.sameSlot += 1;
    else if (value === 1) counts.nextSlot += 1;
    else if (value <= 3) counts.twoToThree += 1;
    else counts.late += 1;
  }
  return counts;
}

function countBy(rows, getKey) {
  const counts = new Map();
  for (const row of rows) {
    const key = getKey(row) || "unknown";
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function isBroadcastRow(row) {
  return row?.kind === "broadcast" || row?.kind === "stage:broadcast";
}

function mdTable(entries) {
  if (entries.length === 0) return "_None._";
  return [
    "| Key | Count |",
    "|---|---:|",
    ...entries.slice(0, 12).map(([key, count]) => `| \`${String(key).replaceAll("|", "\\|")}\` | ${count} |`),
  ].join("\n");
}

const label = arg("label", "Wallet");
const telemetryPath = arg("telemetry", "data/execution-telemetry.jsonl");
const tradesPath = arg("trades", "data/trades.jsonl");
const mdPath = arg("md");
const sinceRaw = arg("since", "today");
const now = new Date();
const since =
  sinceRaw === "today"
    ? new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    : Number.isFinite(Date.parse(sinceRaw))
      ? Date.parse(sinceRaw)
      : 0;

const telemetry = readJsonl(telemetryPath).filter((row) => (num(row.tsMs) ?? Date.parse(row.ts ?? "")) >= since);
const trades = readJsonl(tradesPath).filter((row) => (num(row.ts) ?? Date.parse(row.isoTs ?? "")) >= since);

const successes = telemetry.filter((row) => row.success === true);
const failures = telemetry.filter((row) => row.success === false);
const buys = trades.filter((row) => row.side === "buy");
const sells = trades.filter((row) => row.side === "sell");
const uniqueBoughtMints = new Set(buys.map(tokenMintForTrade));
const realizedPnlLamports = sells.reduce((sum, row) => sum + (num(row.realizedPnlLamports) ?? 0), 0);
const wins = sells.filter((row) => (num(row.realizedPnlLamports) ?? 0) > 0).length;
const marketCapRows = trades.filter((row) => row.marketCapUsd != null);
const missingMarketCapRows = trades.filter((row) => row.marketCapUsd == null);

const chainGroups = new Map();
for (const row of telemetry) {
  if (!row.sourceSignature) continue;
  const group = chainGroups.get(row.sourceSignature) ?? [];
  group.push(row);
  chainGroups.set(row.sourceSignature, group);
}

const chains = [];
for (const [sourceSignature, group] of chainGroups) {
  const sorted = [...group].sort((a, b) => (num(a.tsMs) ?? 0) - (num(b.tsMs) ?? 0));
  const source = sorted.find((row) => row.kind === "source-detected");
  const broadcast = sorted.find((row) => isBroadcastRow(row) && row.success === true);
  const confirm = sorted.find((row) => row.kind === "stage:confirm");
  const basis = source ?? broadcast ?? confirm ?? sorted[0];
  const sourceSlot = detailNum(source ?? basis, "sourceSlot");
  const landingSlot = detailNum(confirm ?? basis, "landingSlot");
  const providerCreatedAt = detailNum(source ?? basis, "providerCreatedAtMs");
  const processStartedAt = detailNum(source ?? basis, "processStartedAtMs");
  const broadcastAt = num(broadcast?.tsMs);
  const confirmAt = num(confirm?.tsMs);
  chains.push({
    sourceSignature,
    mint: source?.mint ?? broadcast?.mint ?? confirm?.mint ?? "unknown",
    detector: source?.detector ?? source?.details?.detector ?? broadcast?.detector ?? "unknown",
    route: broadcast?.route ?? source?.route ?? "unknown",
    side: broadcast?.side ?? source?.side ?? "unknown",
    sourceSlot,
    landingSlot,
    slotDelta: sourceSlot != null && landingSlot != null ? landingSlot - sourceSlot : null,
    providerToBroadcastMs: providerCreatedAt != null && broadcastAt != null ? broadcastAt - providerCreatedAt : null,
    processToBroadcastMs: processStartedAt != null && broadcastAt != null ? broadcastAt - processStartedAt : null,
    broadcastToConfirmMs: broadcastAt != null && confirmAt != null ? confirmAt - broadcastAt : null,
    confirmed: Boolean(confirm?.success),
  });
}

const buyChains = chains.filter((chain) => chain.side === "buy");
const slotBuckets = bucketCounts(buyChains.map((chain) => chain.slotDelta));
const providerToBroadcast = buyChains.map((chain) => chain.providerToBroadcastMs).filter((value) => value != null);
const processToBroadcast = buyChains.map((chain) => chain.processToBroadcastMs).filter((value) => value != null);
const slotDeltas = buyChains.map((chain) => chain.slotDelta).filter((value) => value != null);

const latestChains = buyChains
  .filter((chain) => chain.providerToBroadcastMs != null || chain.slotDelta != null)
  .slice(-12)
  .reverse();

const section = [
  `## Latest VPS Telemetry Scan - ${label}`,
  "",
  `Updated: ${now.toISOString()}`,
  `Window: ${sinceRaw} (${new Date(since).toISOString()})`,
  "",
  "### Trade Sample",
  "",
  `- Trades logged: ${trades.length}`,
  `- Buys: ${buys.length}`,
  `- Unique bought mints: ${uniqueBoughtMints.size}`,
  `- Sells: ${sells.length}`,
  `- Realized PnL: ${fmtSol(realizedPnlLamports)}`,
  `- Closed-trade win rate: ${fmtPct(wins, sells.length)}`,
  `- Trades with market cap recorded: ${marketCapRows.length}`,
  `- Trades missing market cap: ${missingMarketCapRows.length}`,
  "",
  "### Execution Telemetry",
  "",
  `- Telemetry rows: ${telemetry.length}`,
  `- Success rows: ${successes.length} (${fmtPct(successes.length, telemetry.length)})`,
  `- Failure rows: ${failures.length} (${fmtPct(failures.length, telemetry.length)})`,
  `- Buy chains: ${buyChains.length}`,
  `- Same-slot buys: ${slotBuckets.sameSlot} (${fmtPct(slotBuckets.sameSlot, buyChains.length)})`,
  `- Next-slot buys: ${slotBuckets.nextSlot} (${fmtPct(slotBuckets.nextSlot, buyChains.length)})`,
  `- 2-3 slot buys: ${slotBuckets.twoToThree} (${fmtPct(slotBuckets.twoToThree, buyChains.length)})`,
  `- Late buys: ${slotBuckets.late} (${fmtPct(slotBuckets.late, buyChains.length)})`,
  `- Unknown slot delta buys: ${slotBuckets.unknown}`,
  "",
  "### Latency",
  "",
  `- Provider -> broadcast p50/p90/p99: ${fmtMs(quantile(providerToBroadcast, 0.5))} / ${fmtMs(quantile(providerToBroadcast, 0.9))} / ${fmtMs(quantile(providerToBroadcast, 0.99))}`,
  `- Process -> broadcast p50/p90/p99: ${fmtMs(quantile(processToBroadcast, 0.5))} / ${fmtMs(quantile(processToBroadcast, 0.9))} / ${fmtMs(quantile(processToBroadcast, 0.99))}`,
  `- Source slot -> landing slot p50/p90/p99: ${fmtNum(quantile(slotDeltas, 0.5), 0)} / ${fmtNum(quantile(slotDeltas, 0.9), 0)} / ${fmtNum(quantile(slotDeltas, 0.99), 0)}`,
  "",
  "### Routes",
  "",
  mdTable(countBy(telemetry.filter((row) => isBroadcastRow(row)), (row) => row.route)),
  "",
  "### Detectors",
  "",
  mdTable(countBy(telemetry.filter((row) => row.kind === "source-detected"), (row) => row.detector ?? row.details?.detector)),
  "",
  "### Failures",
  "",
  mdTable(countBy(failures, (row) => row.errorName ?? row.errorClass ?? row.errorSummary ?? row.kind)),
  "",
  "### Latest Buy Chains",
  "",
  latestChains.length === 0
    ? "_No buy chains with broadcast/slot data in this window._"
    : [
        "| Source Tx | Mint | Route | Detector | Slot Delta | Provider -> Broadcast | Process -> Broadcast |",
        "|---|---|---|---|---:|---:|---:|",
        ...latestChains.map(
          (chain) =>
            `| \`${short(chain.sourceSignature)}\` | \`${short(chain.mint)}\` | \`${chain.route}\` | \`${chain.detector}\` | ${chain.slotDelta ?? "-"} | ${fmtMs(chain.providerToBroadcastMs)} | ${fmtMs(chain.processToBroadcastMs)} |`
        ),
      ].join("\n"),
  "",
  "### Read",
  "",
  "- Same-slot/next-slot quality is measured by confirmed source slot -> landing slot delta.",
  "- Provider -> broadcast shows bot speed before confirmation noise.",
  "- Missing market-cap rows mean entry MC was not available in the trade log; use slot/latency plus source-wallet research before overfitting.",
  "- If same-slot is low but provider -> broadcast is already low, the bottleneck is likely sender/fee/leader auction rather than detection.",
  "",
].join("\n");

if (mdPath) {
  const absolute = path.resolve(mdPath);
  const old = fs.existsSync(absolute) ? fs.readFileSync(absolute, "utf8") : `# ${label}\n`;
  const marker = `## Latest VPS Telemetry Scan - ${label}`;
  const next = old.includes(marker)
    ? old.replace(new RegExp(`${marker}[\\s\\S]*?(?=\\n## (?!Latest VPS Telemetry Scan - ${label})|$)`), section)
    : `${old.trimEnd()}\n\n${section}\n`;
  fs.writeFileSync(absolute, next, "utf8");
  console.log(`Updated ${absolute}`);
}

console.log(section);
