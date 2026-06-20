import "dotenv/config";
import fs from "node:fs";
import path from "node:path";

const SOL_MINT = "So11111111111111111111111111111111111111112";
const DEFAULT_OMEGO = "omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq";

function arg(name, fallback = "") {
  const prefix = `--${name}=`;
  const found = process.argv.find((value) => value.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

function boolArg(name, fallback = false) {
  const value = arg(name, "");
  if (!value) return fallback;
  return ["true", "1", "yes", "on"].includes(value.toLowerCase());
}

function ensureDir(fileOrDir, isDir = false) {
  const dir = isDir ? fileOrDir : path.dirname(fileOrDir);
  fs.mkdirSync(dir, { recursive: true });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

function tsMs(row) {
  return num(row?.tsMs) ?? num(row?.ts) ?? Date.parse(row?.isoTs ?? row?.ts ?? "");
}

function short(value) {
  return typeof value === "string" && value.length > 12
    ? `${value.slice(0, 8)}...${value.slice(-4)}`
    : value ?? "-";
}

function pct(n, d) {
  return d > 0 ? `${((n / d) * 100).toFixed(1)}%` : "0.0%";
}

function fmtSol(lamports) {
  return `${((lamports ?? 0) / 1_000_000_000).toFixed(4)} SOL`;
}

function fmtDate(secOrMs) {
  if (!secOrMs) return "-";
  const ms = secOrMs > 10_000_000_000 ? secOrMs : secOrMs * 1000;
  return new Date(ms).toISOString();
}

function safeName(value) {
  return String(value).replace(/[^a-zA-Z0-9_.-]/g, "_");
}

function tokenMintForTrade(row) {
  if (row.side === "buy") return row.inputMint === SOL_MINT ? row.outputMint : row.inputMint;
  if (row.side === "sell") return row.inputMint === SOL_MINT ? row.outputMint : row.inputMint;
  return row.outputMint ?? row.inputMint ?? "unknown";
}

function extractSwap(tx, wallet) {
  let nativeNet = 0;
  const tokenNet = new Map();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      if (!mint || mint === SOL_MINT) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      const decimals = change.rawTokenAmount?.decimals ?? 0;
      if (!Number.isFinite(raw) || raw === 0) continue;
      const prev = tokenNet.get(mint) ?? { raw: 0, decimals };
      prev.raw += raw;
      prev.decimals = decimals;
      tokenNet.set(mint, prev);
    }
  }

  let mint = null;
  let tokenRaw = 0;
  let decimals = 0;
  for (const [candidate, net] of tokenNet) {
    if (Math.abs(net.raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = net.raw;
      decimals = net.decimals;
    }
  }

  if (!mint || tokenRaw === 0 || nativeNet === 0) return null;
  if (nativeNet < 0 && tokenRaw > 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      slot: tx.slot ?? null,
      side: "buy",
      mint,
      solLamports: Math.abs(nativeNet),
      tokenRaw: Math.abs(tokenRaw),
      decimals,
    };
  }
  if (nativeNet > 0 && tokenRaw < 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      slot: tx.slot ?? null,
      side: "sell",
      mint,
      solLamports: nativeNet,
      tokenRaw: Math.abs(tokenRaw),
      decimals,
    };
  }
  return null;
}

function addressTxUrl(wallet, before, limit) {
  const apiKey = process.env.HELIUS_API_KEY ?? "";
  if (!apiKey) throw new Error("HELIUS_API_KEY is not set");
  const params = new URLSearchParams({ "api-key": apiKey, limit: String(limit) });
  if (before) params.set("before", before);
  return `https://api-mainnet.helius-rpc.com/v0/addresses/${wallet}/transactions?${params.toString()}`;
}

async function fetchJsonWithRetry(url, sleepMs, maxRetries) {
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
    if (response.ok) return response.json();
    const text = await response.text().catch(() => "");
    if (response.status !== 429 && response.status < 500) {
      throw new Error(`HTTP ${response.status}: ${text.slice(0, 220)}`);
    }
    const retryAfter = Number(response.headers.get("retry-after"));
    const waitMs = Number.isFinite(retryAfter)
      ? retryAfter * 1000
      : sleepMs * Math.max(2, attempt + 1);
    console.warn(`Helius ${response.status}; retrying in ${Math.round(waitMs)}ms`);
    await sleep(waitMs);
  }
  throw new Error(`HTTP retry budget exhausted for ${url}`);
}

async function fetchWalletTransactions({ wallet, sinceMs, untilMs, maxTxs, pageSize, sleepMs, cacheDir, refresh }) {
  ensureDir(cacheDir, true);
  const all = [];
  let before = "";
  let page = 0;
  while (all.length < maxTxs) {
    const cacheKey = path.join(
      cacheDir,
      `${safeName(wallet)}-${page}-${before ? before.slice(0, 16) : "latest"}-${pageSize}.json`
    );
    let batch;
    if (!refresh && fs.existsSync(cacheKey)) {
      batch = JSON.parse(fs.readFileSync(cacheKey, "utf8"));
    } else {
      const url = addressTxUrl(wallet, before, Math.min(pageSize, maxTxs - all.length));
      batch = await fetchJsonWithRetry(url, sleepMs, 8);
      fs.writeFileSync(cacheKey, JSON.stringify(batch), "utf8");
      await sleep(sleepMs);
    }
    if (!Array.isArray(batch) || batch.length === 0) break;
    all.push(...batch);
    const oldestTs = Math.min(...batch.map((tx) => (tx.timestamp ?? 0) * 1000).filter(Number.isFinite));
    if (oldestTs && oldestTs < sinceMs) break;
    before = batch[batch.length - 1]?.signature ?? "";
    if (!before) break;
    page++;
  }
  return all.filter((tx) => {
    const t = (tx.timestamp ?? 0) * 1000;
    return t >= sinceMs && t <= untilMs;
  });
}

function groupBySourceSignature(rows) {
  const grouped = new Map();
  for (const row of rows) {
    if (!row.sourceSignature) continue;
    const group = grouped.get(row.sourceSignature) ?? [];
    group.push(row);
    grouped.set(row.sourceSignature, group);
  }
  return grouped;
}

function summarizeTelemetry(group = []) {
  const kinds = new Set(group.map((row) => row.kind).filter(Boolean));
  const source = group.find((row) => row.kind === "source-detected" || row.kind === "stage:detect");
  const broadcasts = group.filter((row) => row.kind === "broadcast" || row.kind === "stage:broadcast");
  const confirms = group.filter((row) => row.kind === "stage:confirm");
  const failures = group.filter((row) => row.success === false || row.error || row.errorSummary);
  const successBroadcast = broadcasts.find((row) => row.success === true);
  const successConfirm = confirms.find((row) => row.success === true);
  return {
    detected: group.length > 0,
    sourceDetected: Boolean(source),
    attempted: group.some((row) => row.kind?.startsWith?.("stage:") || row.kind === "broadcast"),
    broadcasted: broadcasts.length > 0,
    broadcastSuccess: Boolean(successBroadcast),
    confirmSuccess: Boolean(successConfirm),
    route: successBroadcast?.route ?? broadcasts[0]?.route ?? group.find((row) => row.route)?.route ?? null,
    detector: source?.detector ?? source?.details?.detector ?? group.find((row) => row.detector)?.detector ?? null,
    error: failures[0]?.errorSummary ?? failures[0]?.errorName ?? failures[0]?.error ?? null,
    kinds: [...kinds].sort(),
  };
}

function makeMarkdown(summary, rows, options) {
  const byStatus = new Map();
  for (const row of rows) byStatus.set(row.status, (byStatus.get(row.status) ?? 0) + 1);
  const statusRows = [...byStatus.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([key, value]) => `| \`${key}\` | ${value} | ${pct(value, rows.length)} |`)
    .join("\n");

  const missedBuys = rows.filter((row) => row.side === "buy" && row.status !== "captured");
  const staleRiskSells = rows.filter((row) => row.side === "sell" && row.stalePositionRisk);
  const recentIssues = rows
    .filter((row) => row.status !== "captured" || row.stalePositionRisk)
    .slice(-40)
    .reverse()
    .map((row) => {
      const flags = [
        row.status,
        row.stalePositionRisk ? "stale-risk" : "",
        row.telemetry?.error ? `err=${row.telemetry.error}` : "",
      ].filter(Boolean).join(", ");
      return `| ${fmtDate(row.timestamp)} | ${row.side} | \`${short(row.mint)}\` | \`${short(row.sourceSignature)}\` | ${fmtSol(row.solLamports)} | ${flags} |`;
    })
    .join("\n");

  return [
    `# Source Coverage Audit - ${options.label}`,
    "",
    `Updated: ${new Date().toISOString()}`,
    `Wallet: \`${options.wallet}\``,
    `Window: ${new Date(options.sinceMs).toISOString()} -> ${new Date(options.untilMs).toISOString()}`,
    "",
    "## Summary",
    "",
    `- Source swaps found: ${summary.sourceSwaps}`,
    `- Source buys/sells: ${summary.sourceBuys} / ${summary.sourceSells}`,
    `- Captured trades: ${summary.captured}`,
    `- Not captured: ${summary.notCaptured}`,
    `- Source buys without matching bot buy: ${missedBuys.length}`,
    `- Source sells with stale-position risk: ${staleRiskSells.length}`,
    `- Detection-only rows: ${summary.detectedNoTrade}`,
    `- No telemetry/no trade rows: ${summary.notDetected}`,
    "",
    "## Status Breakdown",
    "",
    "| Status | Count | Share |",
    "|---|---:|---:|",
    statusRows || "| _none_ | 0 | 0.0% |",
    "",
    "## Recent Issues",
    "",
    recentIssues
      ? [
          "| Time | Side | Mint | Source Tx | SOL | Flags |",
          "|---|---|---|---|---:|---|",
          recentIssues,
        ].join("\n")
      : "_No missed or stale-risk rows in this window._",
    "",
    "## Read",
    "",
    "- `captured` means a matching trade log row exists for the source signature and side.",
    "- `detected-no-trade` means telemetry saw the source tx, but no matching trade was written.",
    "- `not-detected` means neither telemetry nor trade logs contain the source signature.",
    "- `stale-position risk` means the source sold while the audit believed our bot still had a captured buy for that mint.",
    "- If telemetry was disabled, this can still find trade-log gaps, but it cannot prove why a source tx was missed.",
    "",
  ].join("\n");
}

const wallet = arg("wallet", DEFAULT_OMEGO);
const label = arg("label", wallet === DEFAULT_OMEGO ? "Omego" : "Wallet");
const sinceRaw = arg("since", new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString());
const untilRaw = arg("until", new Date().toISOString());
const sinceMs = Date.parse(sinceRaw);
const untilMs = Date.parse(untilRaw);
if (!Number.isFinite(sinceMs) || !Number.isFinite(untilMs)) {
  throw new Error("Invalid --since or --until. Use ISO timestamps.");
}

const maxTxs = Number(arg("max-txs", "50000"));
const pageSize = Number(arg("page-size", "100"));
const sleepMs = Number(arg("sleep-ms", "1250"));
const telemetryPath = arg("telemetry", "data/execution-telemetry.jsonl");
const tradesPath = arg("trades", "data/trades.jsonl");
const cacheDir = arg("cache-dir", "data/source-coverage-cache");
const outJson = arg("out-json", `data/source-coverage-${safeName(label.toLowerCase())}.json`);
const outMd = arg("out-md", `data/source-coverage-${safeName(label.toLowerCase())}.md`);
const refresh = boolArg("refresh", false);

const txs = await fetchWalletTransactions({ wallet, sinceMs, untilMs, maxTxs, pageSize, sleepMs, cacheDir, refresh });
const sourceSwaps = txs
  .map((tx) => extractSwap(tx, wallet))
  .filter(Boolean)
  .sort((a, b) => a.timestamp - b.timestamp);

const telemetry = readJsonl(telemetryPath).filter((row) => {
  const t = tsMs(row);
  return Number.isFinite(t) && t >= sinceMs && t <= untilMs;
});
const trades = readJsonl(tradesPath).filter((row) => {
  const t = tsMs(row);
  return Number.isFinite(t) && t >= sinceMs && t <= untilMs;
});

const telemetryBySource = groupBySourceSignature(telemetry);
const tradesBySource = groupBySourceSignature(trades);
const openCapturedMints = new Set();

const rows = sourceSwaps.map((swap) => {
  const tradeGroup = tradesBySource.get(swap.signature) ?? [];
  const matchingTrade = tradeGroup.find((trade) => trade.side === swap.side && tokenMintForTrade(trade) === swap.mint)
    ?? tradeGroup.find((trade) => trade.side === swap.side)
    ?? null;
  const telemetrySummary = summarizeTelemetry(telemetryBySource.get(swap.signature) ?? []);
  let stalePositionRisk = false;
  if (swap.side === "buy" && matchingTrade) {
    openCapturedMints.add(swap.mint);
  } else if (swap.side === "sell") {
    stalePositionRisk = openCapturedMints.has(swap.mint) && !matchingTrade;
    if (matchingTrade) openCapturedMints.delete(swap.mint);
  }
  const status = matchingTrade
    ? "captured"
    : telemetrySummary.detected
      ? "detected-no-trade"
      : "not-detected";
  return {
    sourceSignature: swap.signature,
    timestamp: swap.timestamp,
    slot: swap.slot,
    side: swap.side,
    mint: swap.mint,
    solLamports: swap.solLamports,
    tokenRaw: swap.tokenRaw,
    status,
    stalePositionRisk,
    trade: matchingTrade
      ? {
          mirrorSignature: matchingTrade.mirrorSignature ?? null,
          side: matchingTrade.side,
          mint: tokenMintForTrade(matchingTrade),
          error: matchingTrade.error ?? null,
        }
      : null,
    telemetry: telemetrySummary,
  };
});

const summary = {
  label,
  wallet,
  since: new Date(sinceMs).toISOString(),
  until: new Date(untilMs).toISOString(),
  sourceTxs: txs.length,
  sourceSwaps: sourceSwaps.length,
  sourceBuys: rows.filter((row) => row.side === "buy").length,
  sourceSells: rows.filter((row) => row.side === "sell").length,
  captured: rows.filter((row) => row.status === "captured").length,
  notCaptured: rows.filter((row) => row.status !== "captured").length,
  detectedNoTrade: rows.filter((row) => row.status === "detected-no-trade").length,
  notDetected: rows.filter((row) => row.status === "not-detected").length,
  stalePositionRisk: rows.filter((row) => row.stalePositionRisk).length,
  telemetryRows: telemetry.length,
  tradeRows: trades.length,
};

const output = { summary, rows };
ensureDir(outJson);
fs.writeFileSync(outJson, JSON.stringify(output, null, 2), "utf8");
fs.writeFileSync(outMd, makeMarkdown(summary, rows, { label, wallet, sinceMs, untilMs }), "utf8");

console.log(`Wrote ${outJson}`);
console.log(`Wrote ${outMd}`);
console.log(JSON.stringify(summary, null, 2));
