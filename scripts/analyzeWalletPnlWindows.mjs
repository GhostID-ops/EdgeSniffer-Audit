import "dotenv/config";
import fs from "node:fs/promises";
import path from "node:path";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
const DEFAULT_WALLET = "omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq";
const CACHE_DIR = path.resolve(process.cwd(), "data", "wallet-pnl-window-cache");
const KNOWN_CACHE_DIRS = [
  path.resolve(process.cwd(), "data", "wallet-ledger-cache"),
  path.resolve(process.cwd(), "data", "wallet-quant-audit-cache"),
  path.resolve(process.cwd(), "data", "path-audit-cache"),
];

function parseArgs() {
  const args = {
    wallet: DEFAULT_WALLET,
    maxTxs: 50_000,
    fetch: false,
    cacheOnly: false,
    anchor: "now",
  };
  for (const arg of process.argv.slice(2)) {
    if (arg === "--fetch") args.fetch = true;
    else if (arg === "--cache-only") args.cacheOnly = true;
    else if (arg === "--anchor=newest") args.anchor = "newest";
    else if (arg === "--anchor=now") args.anchor = "now";
    else if (arg.startsWith("--wallet=")) args.wallet = arg.slice("--wallet=".length);
    else if (arg.startsWith("--max-txs=")) args.maxTxs = Number(arg.slice("--max-txs=".length));
  }
  return args;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sol(lamports) {
  return lamports / 1_000_000_000;
}

function fmtSol(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(4)} SOL`;
}

function fmtPct(value) {
  if (value == null || !Number.isFinite(value)) return "-";
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function fmtNum(value, digits = 2) {
  if (value == null || !Number.isFinite(value)) return "-";
  return value === Infinity ? "inf" : value.toFixed(digits);
}

function short(value) {
  return value ? `${value.slice(0, 6)}...${value.slice(-4)}` : "-";
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function avg(values) {
  const xs = values.filter(Number.isFinite);
  return xs.length ? xs.reduce((sum, value) => sum + value, 0) / xs.length : null;
}

function profitFactor(values) {
  const profit = values.filter((value) => value > 0).reduce((sum, value) => sum + value, 0);
  const loss = Math.abs(values.filter((value) => value < 0).reduce((sum, value) => sum + value, 0));
  if (loss === 0) return profit > 0 ? Infinity : null;
  return profit / loss;
}

async function readJsonIfExists(file) {
  try {
    return JSON.parse(await fs.readFile(file, "utf8"));
  } catch {
    return null;
  }
}

async function loadKnownCaches(wallet) {
  const bySig = new Map();
  const sources = [];
  for (const dir of KNOWN_CACHE_DIRS) {
    let files = [];
    try {
      files = await fs.readdir(dir);
    } catch {
      continue;
    }
    for (const file of files) {
      if (!file.includes(wallet) || !file.endsWith(".json")) continue;
      const fullPath = path.join(dir, file);
      const rows = await readJsonIfExists(fullPath);
      if (!Array.isArray(rows)) continue;
      sources.push({ file: path.relative(process.cwd(), fullPath), rows: rows.length });
      for (const tx of rows) {
        if (tx?.signature) bySig.set(tx.signature, tx);
      }
    }
  }
  return { txs: [...bySig.values()], sources };
}

function heliusAddressTransactionsUrl(wallet, before, limit) {
  const key = process.env.HELIUS_API_KEY ?? "";
  if (!key) return "";
  const url = new URL(`https://api.helius.xyz/v0/addresses/${wallet}/transactions`);
  url.searchParams.set("api-key", key);
  url.searchParams.set("limit", String(limit));
  if (before) url.searchParams.set("before", before);
  return url.toString();
}

async function fetchWalletTxs(wallet, maxTxs) {
  const cacheFile = path.join(CACHE_DIR, `wallet-${wallet}-${maxTxs}.json`);
  const cached = await readJsonIfExists(cacheFile);
  if (Array.isArray(cached) && cached.length) {
    return { txs: cached, sources: [{ file: path.relative(process.cwd(), cacheFile), rows: cached.length }] };
  }

  await fs.mkdir(CACHE_DIR, { recursive: true });
  const all = [];
  let before;
  while (all.length < maxTxs) {
    const url = heliusAddressTransactionsUrl(wallet, before, Math.min(100, maxTxs - all.length));
    if (!url) throw new Error("HELIUS_API_KEY is not set. Use --cache-only or set HELIUS_API_KEY.");
    const response = await fetch(url, { signal: AbortSignal.timeout(20_000) });
    if (response.status === 429) {
      await sleep(65_000);
      continue;
    }
    if (!response.ok) throw new Error(`Helius ${response.status}: ${(await response.text()).slice(0, 200)}`);
    const batch = await response.json();
    if (!Array.isArray(batch) || batch.length === 0) break;
    all.push(...batch);
    before = batch.at(-1)?.signature;
    console.log(`Fetched ${all.length}/${maxTxs} txs...`);
    await sleep(750);
  }
  await fs.writeFile(cacheFile, JSON.stringify(all), "utf8");
  return { txs: all, sources: [{ file: path.relative(process.cwd(), cacheFile), rows: all.length }] };
}

function extractSwap(tx, wallet) {
  let nativeLamports = 0;
  let wsolRaw = 0;
  let usdcRaw = 0;
  const tokenNet = new Map();
  const decimalsByMint = new Map();

  for (const account of tx.accountData ?? []) {
    if (account.account === wallet) nativeLamports += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== wallet) continue;
      const mint = change.mint;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      const decimals = change.rawTokenAmount?.decimals ?? 0;
      if (!mint || !Number.isFinite(raw) || raw === 0) continue;
      if (mint === WRAPPED_SOL_MINT) wsolRaw += raw;
      else if (mint === USDC_MINT) usdcRaw += raw;
      else {
        tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
        decimalsByMint.set(mint, decimals);
      }
    }
  }

  const quoteLamports = nativeLamports + wsolRaw;
  let mint = null;
  let tokenRaw = 0;
  for (const [candidate, raw] of tokenNet) {
    if (Math.abs(raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = raw;
    }
  }
  if (!mint || tokenRaw === 0 || quoteLamports === 0 || usdcRaw !== 0) return null;
  const feeLamports = tx.feePayer === wallet ? Number(tx.fee ?? 0) || 0 : 0;
  if (tokenRaw > 0 && quoteLamports < 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      slot: tx.slot ?? null,
      side: "buy",
      mint,
      decimals: decimalsByMint.get(mint) ?? 0,
      tokenRaw: Math.abs(tokenRaw),
      quoteLamports: Math.abs(quoteLamports),
      feeLamports,
      source: tx.source ?? "",
    };
  }
  if (tokenRaw < 0 && quoteLamports > 0) {
    return {
      signature: tx.signature,
      timestamp: tx.timestamp ?? 0,
      slot: tx.slot ?? null,
      side: "sell",
      mint,
      decimals: decimalsByMint.get(mint) ?? 0,
      tokenRaw: Math.abs(tokenRaw),
      quoteLamports,
      feeLamports,
      source: tx.source ?? "",
    };
  }
  return null;
}

function consume(lots, rawToSell) {
  let remaining = rawToSell;
  let grossCost = 0;
  let netCost = 0;
  let rawCosted = 0;
  while (remaining > 0 && lots.length) {
    const lot = lots[0];
    const take = Math.min(remaining, lot.raw);
    const ratio = take / lot.raw;
    grossCost += lot.grossCostLamports * ratio;
    netCost += lot.netCostLamports * ratio;
    rawCosted += take;
    lot.raw -= take;
    lot.grossCostLamports -= lot.grossCostLamports * ratio;
    lot.netCostLamports -= lot.netCostLamports * ratio;
    remaining -= take;
    if (lot.raw <= 1) lots.shift();
  }
  return { grossCost, netCost, rawCosted, rawUncosted: Math.max(0, rawToSell - rawCosted) };
}

function audit(swaps) {
  const positions = new Map();
  const realized = [];
  const uncostedSells = [];
  for (const swap of [...swaps].sort((a, b) => a.timestamp - b.timestamp || (a.slot ?? 0) - (b.slot ?? 0))) {
    let pos = positions.get(swap.mint);
    if (!pos) {
      pos = { mint: swap.mint, decimals: swap.decimals, lots: [], buys: 0, sells: 0 };
      positions.set(swap.mint, pos);
    }
    if (swap.side === "buy") {
      pos.buys += 1;
      pos.lots.push({
        raw: swap.tokenRaw,
        grossCostLamports: swap.quoteLamports,
        netCostLamports: swap.quoteLamports + swap.feeLamports,
      });
      continue;
    }

    pos.sells += 1;
    const soldRaw = swap.tokenRaw;
    const { grossCost, netCost, rawCosted, rawUncosted } = consume(pos.lots, soldRaw);
    if (rawUncosted > 0) {
      uncostedSells.push({ ...swap, rawUncosted });
    }
    if (rawCosted <= 0 || grossCost <= 0) continue;

    const costedRatio = rawCosted / soldRaw;
    const grossProceeds = swap.quoteLamports * costedRatio;
    const netProceeds = Math.max(0, swap.quoteLamports - swap.feeLamports) * costedRatio;
    realized.push({
      signature: swap.signature,
      timestamp: swap.timestamp,
      mint: swap.mint,
      grossPnlLamports: grossProceeds - grossCost,
      netPnlLamports: netProceeds - netCost,
      grossCostLamports: grossCost,
      netCostLamports: netCost,
      grossProceedsLamports: grossProceeds,
      netProceedsLamports: netProceeds,
      grossRoiPct: ((grossProceeds - grossCost) / grossCost) * 100,
      netRoiPct: ((netProceeds - netCost) / netCost) * 100,
      feeLamports: swap.feeLamports,
    });
  }
  return { realized, uncostedSells, positions };
}

function summarizeWindow(name, startTs, endTs, swaps, realized) {
  const windowSwaps = swaps.filter((swap) => swap.timestamp >= startTs && swap.timestamp <= endTs);
  const buys = windowSwaps.filter((swap) => swap.side === "buy");
  const sells = windowSwaps.filter((swap) => swap.side === "sell");
  const rows = realized.filter((row) => row.timestamp >= startTs && row.timestamp <= endTs);
  const netPnl = rows.map((row) => sol(row.netPnlLamports));
  const grossPnlLamports = rows.reduce((sum, row) => sum + row.grossPnlLamports, 0);
  const netPnlLamports = rows.reduce((sum, row) => sum + row.netPnlLamports, 0);
  const netCostLamports = rows.reduce((sum, row) => sum + row.netCostLamports, 0);
  return {
    name,
    startIso: new Date(startTs * 1000).toISOString(),
    endIso: new Date(endTs * 1000).toISOString(),
    buys: buys.length,
    sells: sells.length,
    uniqueBuyMints: new Set(buys.map((swap) => swap.mint)).size,
    uniqueSellMints: new Set(sells.map((swap) => swap.mint)).size,
    deployedSol: sol(buys.reduce((sum, swap) => sum + swap.quoteLamports, 0)),
    grossPnlSol: sol(grossPnlLamports),
    netPnlSol: sol(netPnlLamports),
    netRoiPct: netCostLamports > 0 ? (netPnlLamports / netCostLamports) * 100 : null,
    realizedSells: rows.length,
    winRatePct: rows.length ? (rows.filter((row) => row.netPnlLamports > 0).length / rows.length) * 100 : null,
    avgTradePnlSol: avg(netPnl),
    medianTradePnlSol: median(netPnl),
    profitFactor: profitFactor(netPnl),
    feesSol: sol(windowSwaps.reduce((sum, swap) => sum + swap.feeLamports, 0)),
  };
}

function renderTable(rows) {
  const lines = [
    "| Window | Buys | Sells | Buy mints | Deployed | Gross PnL | Net PnL | Net ROI | Win % | PF | Fees | Coverage |",
    "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
  ];
  for (const row of rows) {
    lines.push(
      `| ${row.name} | ${row.buys} | ${row.sells} | ${row.uniqueBuyMints} | ${fmtSol(row.deployedSol)} | ${fmtSol(row.grossPnlSol)} | ${fmtSol(row.netPnlSol)} | ${fmtPct(row.netRoiPct)} | ${fmtPct(row.winRatePct)} | ${fmtNum(row.profitFactor)} | ${fmtSol(row.feesSol)} | ${row.startIso} -> ${row.endIso} |`
    );
  }
  return lines.join("\n");
}

async function main() {
  const args = parseArgs();
  const fetched = args.fetch && !args.cacheOnly ? await fetchWalletTxs(args.wallet, args.maxTxs) : { txs: [], sources: [] };
  const known = await loadKnownCaches(args.wallet);
  const txBySig = new Map();
  for (const tx of known.txs) txBySig.set(tx.signature, tx);
  for (const tx of fetched.txs) txBySig.set(tx.signature, tx);
  const txs = [...txBySig.values()];
  const swaps = txs.map((tx) => extractSwap(tx, args.wallet)).filter((swap) => swap && swap.timestamp > 0);
  if (!swaps.length) throw new Error("No swaps found in cache/fetch results.");
  const { realized, uncostedSells } = audit(swaps);

  const oldestTs = Math.min(...swaps.map((swap) => swap.timestamp));
  const newestTs = Math.max(...swaps.map((swap) => swap.timestamp));
  const nowTs = Math.floor(Date.now() / 1000);
  const anchorTs = args.anchor === "newest" ? newestTs : nowTs;
  const windows = [1, 7, 30].map((days) => summarizeWindow(`${days}d`, anchorTs - days * 86400, anchorTs, swaps, realized));

  const out = {
    wallet: args.wallet,
    generatedAt: new Date().toISOString(),
    method: "FIFO per mint from Helius account deltas. Gross excludes fees; net includes wallet-paid tx fees in buy cost and sell proceeds.",
    anchor: args.anchor,
    coverage: {
      transactions: txs.length,
      swaps: swaps.length,
      realizedSells: realized.length,
      uncostedSells: uncostedSells.length,
      oldestIso: new Date(oldestTs * 1000).toISOString(),
      newestIso: new Date(newestTs * 1000).toISOString(),
      sources: [...known.sources, ...fetched.sources],
    },
    windows,
    uncostedSellExamples: uncostedSells.slice(-20).map((row) => ({
      signature: row.signature,
      mint: row.mint,
      timestamp: row.timestamp,
      rawUncosted: row.rawUncosted,
    })),
  };

  await fs.mkdir(path.resolve(process.cwd(), "data"), { recursive: true });
  const jsonPath = path.resolve(process.cwd(), `data/wallet-pnl-windows-${args.wallet}.json`);
  const mdPath = path.resolve(process.cwd(), `data/wallet-pnl-windows-${args.wallet}.md`);
  await fs.writeFile(jsonPath, JSON.stringify(out, null, 2), "utf8");
  const md = [
    `# Wallet PnL Windows - ${short(args.wallet)}`,
    "",
    `Generated: ${out.generatedAt}`,
    "",
    "## Method",
    "",
    out.method,
    "",
    "Important: this is the comparable definition we control. GMGN/Padre/Axiom may differ by open-position marks, USD conversion time, fees, failed tx handling, transfers, and whether they count only closed positions.",
    "",
    "## Coverage",
    "",
    `- Transactions loaded: ${out.coverage.transactions}`,
    `- Swaps extracted: ${out.coverage.swaps}`,
    `- Realized costed sells: ${out.coverage.realizedSells}`,
    `- Uncosted sells: ${out.coverage.uncostedSells}`,
    `- Oldest swap: ${out.coverage.oldestIso}`,
    `- Newest swap: ${out.coverage.newestIso}`,
    `- Anchor: ${args.anchor} (${new Date(anchorTs * 1000).toISOString()})`,
    "",
    "Sources:",
    ...out.coverage.sources.map((source) => `- ${source.file}: ${source.rows} txs`),
    "",
    "## PnL Windows",
    "",
    renderTable(windows),
    "",
    "## Notes",
    "",
    "- Net PnL is the number to compare against platforms that include fees.",
    "- Gross PnL is better for evaluating strategy edge before execution costs.",
    "- If coverage does not span the full 30d window, the 30d row is incomplete by definition.",
    "- Uncosted sells usually mean the cache did not include the buy-side history for that token or the wallet received/transferred tokens outside a normal swap.",
    "",
  ].join("\n");
  await fs.writeFile(mdPath, md, "utf8");
  console.log(md);
  console.log(`Wrote ${path.relative(process.cwd(), jsonPath)}`);
  console.log(`Wrote ${path.relative(process.cwd(), mdPath)}`);
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
