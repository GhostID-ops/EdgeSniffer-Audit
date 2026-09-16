#!/usr/bin/env node
import fs from "node:fs";
import readline from "node:readline";
import zlib from "node:zlib";
import crypto from "node:crypto";

const baseDir = process.env.TEACHER_IMPULSE_BASE_DIR
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1";
const inputsFile = process.env.TEACHER_IMPULSE_INPUTS_FILE || `${baseDir}/decision-inputs.jsonl`;
const capsulesFile = process.env.TEACHER_IMPULSE_CAPSULES_FILE || `${baseDir}/candidate-capsules.jsonl`;
const auditFile = process.env.RUST_SELECTED_CANDIDATE_AUDIT_FILE || `${baseDir}/live-mc5000-dust/rust-selected-candidate-audit.jsonl`;
const selectedFile = process.env.TEACHER_IMPULSE_SELECTED_EVENT_OUTPUT || `${baseDir}/live-mc5000-selected-events.jsonl`;
const receiptsFile = process.env.RECEIPTS_FILE || `${baseDir}/decision-receipts.jsonl`;

const sinceMs = Date.parse(process.env.REPORT_SINCE || "2026-08-29T23:06:27Z");
const tpPct = Number(process.env.PRIMARY_TP_PCT || 20);
const slPct = Number(process.env.PRIMARY_SL_PCT || -20);
const holdMs = Number(process.env.PRIMARY_HOLD_MS || 120000);
const pumpFeeBps = Number(process.env.PUMPFUN_TOTAL_FEE_BPS || 125);
const defaultExecutionFeeLamports = Number(process.env.EXECUTION_FEE_LAMPORTS || 15000);
const maxCapsuleMc = Number(process.env.MAX_CAPSULE_MC_USD || 500000);
const minReportRows = Number(process.env.MIN_REPORT_ROWS || 20);

const LAMPORTS_PER_SOL = 1_000_000_000n;
const INITIAL_VSOL = 30_000_000_000n;
const INITIAL_VTOK = 1_073_000_000_000_000n;

const sizeSols = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1];
const entryDelaysMs = [0, 25, 50, 75, 100, 125, 150, 175, 200, 250];
const exitDelaysMs = [0, 25, 50, 75, 100, 150, 200, 250, 500, 1000];
const maxOpenValues = [1, 2, 3, 4];
const tpGrid = [20, 25, 30, 35];
const slGrid = [-15, -20, -25, -30];
const holdGrid = [30000, 45000, 60000, 90000];

function num(value, fallback = null) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function get(obj, path, fallback = null) {
  let current = obj;
  for (const part of path.split(".")) {
    if (current == null || typeof current !== "object") return fallback;
    current = current[part];
  }
  return current ?? fallback;
}

function rowTime(row) {
  return num(row?.atMs)
    ?? num(row?.entryAtMs)
    ?? num(row?.decidedAtMs)
    ?? num(row?.candidate_ready_ts_ms)
    ?? num(row?.provider?.candidate_ready_ts_ms)
    ?? num(row?.observedAtMs)
    ?? num(row?.canonical_state?.observed_at_ms)
    ?? num(row?.directEntryQuote?.curveSnapshot?.observedAtMs)
    ?? Date.parse(row?.at || "");
}

function rowMint(row) {
  return row?.mint || row?.candidate?.mint || row?.raw_record?.mint || row?.extra?.mint || null;
}

function fmt(value, digits = 2) {
  return Number.isFinite(value) ? Number(value.toFixed(digits)) : null;
}

function fmtPct(value) {
  return Number.isFinite(value) ? `${fmt(value, 2)}%` : "n/a";
}

function fmtSol(value) {
  return Number.isFinite(value) ? `${fmt(value, 5)} SOL` : "n/a";
}

function pct(n, d) {
  return d ? (n / d) * 100 : 0;
}

function sum(xs) {
  return xs.filter(Number.isFinite).reduce((a, b) => a + b, 0);
}

function mean(xs) {
  const vals = xs.filter(Number.isFinite);
  return vals.length ? sum(vals) / vals.length : null;
}

function median(xs) {
  const vals = xs.filter(Number.isFinite).sort((a, b) => a - b);
  if (!vals.length) return null;
  const mid = Math.floor(vals.length / 2);
  return vals.length % 2 ? vals[mid] : (vals[mid - 1] + vals[mid]) / 2;
}

function quantile(xs, q) {
  const vals = xs.filter(Number.isFinite).sort((a, b) => a - b);
  if (!vals.length) return null;
  const idx = Math.min(vals.length - 1, Math.max(0, Math.floor((vals.length - 1) * q)));
  return vals[idx];
}

function pf(pnls) {
  const gp = sum(pnls.filter((x) => x > 0));
  const gl = Math.abs(sum(pnls.filter((x) => x < 0)));
  return gl > 0 ? gp / gl : gp > 0 ? Infinity : 0;
}

function markdownTable(rows, columns) {
  if (!rows.length) return "No rows.";
  return [
    `| ${columns.map((c) => c.label).join(" | ")} |`,
    `| ${columns.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${columns.map((c) => String(c.value(row) ?? "").replaceAll("|", "/")).join(" | ")} |`),
  ].join("\n");
}

function createLineReader(file) {
  const stream = fs.createReadStream(file);
  const input = file.endsWith(".gz") ? stream.pipe(zlib.createGunzip()) : stream;
  return readline.createInterface({ input, crlfDelay: Infinity });
}

async function* readJsonl(file) {
  if (!fs.existsSync(file)) return;
  const rl = createLineReader(file);
  for await (const line of rl) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      Object.defineProperty(row, "__rawLine", { value: line, enumerable: false });
      yield row;
    } catch {
      // Ignore corrupt/truncated lines in research mode.
    }
  }
}

function shaLine(row) {
  return crypto.createHash("sha256").update(row.__rawLine || JSON.stringify(row)).digest("hex");
}

function parseReservesFromStateIdentity(identity) {
  const parts = String(identity || "").split(":");
  if (parts.length < 4 || parts[0] !== "native-shredstream-replay") return null;
  try {
    const vSol = BigInt(parts.at(-2));
    const vTok = BigInt(parts.at(-1));
    if (vSol <= 0n || vTok <= 0n) return null;
    return { vSol, vTok };
  } catch {
    return null;
  }
}

function reservesFromRow(row) {
  const snap = row?.directEntryQuote?.curveSnapshot;
  if (snap?.virtualSolReserves && snap?.virtualTokenReserves) {
    return { vSol: BigInt(snap.virtualSolReserves), vTok: BigInt(snap.virtualTokenReserves) };
  }
  return parseReservesFromStateIdentity(row?.canonical_state?.state_identity);
}

function curveSolForExactSpend(spendLamports, feeBps = pumpFeeBps) {
  const spend = BigInt(Math.max(0, Math.floor(spendLamports)));
  if (spend <= 0n) return 0n;
  const bps = BigInt(Math.min(2000, Math.max(0, Math.floor(feeBps))));
  let net = (spend * 10000n) / (10000n + bps);
  const fee = ceilDiv(net * bps, 10000n);
  const adjustment = net + fee > spend ? net + fee - spend : 0n;
  if (adjustment > 0n) net = net > adjustment ? net - adjustment : 0n;
  return net > 0n ? net - 1n : 0n;
}

function ceilDiv(a, b) {
  return (a + b - 1n) / b;
}

function quoteBuyTokens(state, spendLamports) {
  const curveSol = curveSolForExactSpend(spendLamports);
  if (!state || state.vSol <= 0n || state.vTok <= 0n || curveSol <= 0n) return null;
  const k = state.vSol * state.vTok;
  const newSol = state.vSol + curveSol;
  const newTok = k / newSol;
  const out = state.vTok > newTok ? state.vTok - newTok : 0n;
  return out > 0n ? { tokensOut: out, curveSol, post: { vSol: newSol, vTok: newTok } } : null;
}

function quoteSellLamports(state, tokensInRaw) {
  const tokens = BigInt(tokensInRaw || 0);
  if (!state || state.vSol <= 0n || state.vTok <= 0n || tokens <= 0n) return null;
  const postTok = state.vTok + tokens;
  const k = state.vSol * state.vTok;
  const postSol = k / postTok;
  const gross = state.vSol > postSol ? state.vSol - postSol : 0n;
  const fee = (gross * BigInt(pumpFeeBps)) / 10000n;
  return gross > fee ? { netOut: gross - fee, grossOut: gross, post: { vSol: postSol, vTok: postTok } } : null;
}

function applyExternalEvent(state, event) {
  if (!state) return null;
  if (event.side === "buy" && Number.isFinite(event.quoteLamports) && event.quoteLamports > 0) {
    const q = quoteBuyTokens(state, event.quoteLamports);
    return q?.post || state;
  }
  if (event.side === "sell" && event.sellTokenAmountRaw) {
    const q = quoteSellLamports(state, event.sellTokenAmountRaw);
    return q?.post || state;
  }
  return state;
}

function mcUsdFromReserves(state, solPriceUsd = null) {
  if (!state || state.vSol <= 0n || state.vTok <= 0n) return null;
  const mcSol = Number(state.vSol) / 1e9 * (1_000_000_000_000_000 / Number(state.vTok));
  return Number.isFinite(solPriceUsd) ? mcSol * solPriceUsd : null;
}

function inferSolPrice(row, state) {
  const mcUsd = feature(row, "entry_mc_usd");
  if (!Number.isFinite(mcUsd) || !state) return null;
  const mcSol = Number(state.vSol) / 1e9 * (1_000_000_000_000_000 / Number(state.vTok));
  return mcSol > 0 ? mcUsd / mcSol : null;
}

async function loadReceipts() {
  const receipts = [];
  const inputHashes = new Set();
  const statuses = new Map();
  for await (const row of readJsonl(receiptsFile)) {
    if (rowTime(row) < sinceMs) continue;
    statuses.set(row.status || "missing", (statuses.get(row.status || "missing") || 0) + 1);
    if (row.status !== "accepted" || !row.input_hash || !row.mint) continue;
    receipts.push(row);
    inputHashes.add(row.input_hash);
  }
  return { receipts, inputHashes, statuses };
}

async function loadInputs(inputHashes) {
  const inputs = new Map();
  let scanned = 0;
  for await (const row of readJsonl(inputsFile)) {
    scanned += 1;
    const hash = shaLine(row);
    if (inputHashes.has(hash) && !inputs.has(hash)) inputs.set(hash, row);
  }
  return { inputs, scanned };
}

function hydrateFeatures(input, receipt) {
  const c = input.candidate || {};
  const e = input.execution || {};
  return {
    entry_mc_usd: num(receipt.calculated_market_cap_usd) ?? num(c.market_cap_usd) ?? num(input.canonical_state?.market_cap_usd),
    creation_age_ms: num(c.creation_age_ms),
    net_buy_sol_60: num(c.net_buy_sol_60),
    buy_count_100ms: num(c.buy_count_100ms),
    buy_count_250ms: num(c.buy_count_250ms),
    buy_count_500ms: num(c.buy_count_500ms),
    buy_count_1000ms: num(c.buy_count_1000ms),
    buy_count_60: num(c.buy_count_60),
    sell_pressure_60: num(c.sell_pressure_60),
    dominant_buy_share_60: num(c.dominant_buy_share_60),
    unique_wallets_60: num(c.unique_wallets_60),
    first_buyer_quote_share_bps: num(c.first_buyer_quote_share_bps),
    largest_wallet_quote_share_bps: num(c.largest_wallet_quote_share_bps),
    position_size_lamports: num(e.position_size_lamports, 1_000_000),
    priority_fee_lamports: num(e.priority_fee_lamports, 0),
    jito_tip_lamports: num(e.jito_tip_lamports, 0),
    other_tip_lamports: num(e.other_tip_lamports, 0),
    expected_tokens_out_raw: num(e.expected_tokens_out_raw),
  };
}

function feature(row, name) {
  return num(row.features?.[name]);
}

function buildRows(receipts, inputs) {
  const rows = [];
  for (const receipt of receipts) {
    const input = inputs.get(receipt.input_hash);
    if (!input) continue;
    const entryAtMs = num(get(input, "provider.candidate_ready_ts_ms")) ?? rowTime(input);
    const reserves = reservesFromRow(input);
    const features = hydrateFeatures(input, receipt);
    rows.push({
      mint: receipt.mint,
      inputHash: receipt.input_hash,
      entryAtMs,
      opportunityKey: `${receipt.mint}:${Math.floor(entryAtMs / 250)}`,
      features,
      entryReserves: reserves,
      solPriceUsd: inferSolPrice({ features }, reserves),
      tokenProgram: input.candidate?.token_program || null,
      bondingCurve: input.extra_decision_fields?.bondingCurve || null,
      sourceSlot: num(receipt.source_slot) ?? num(get(input, "provider.source_slot")),
    });
  }
  return dedupe(rows);
}

function dedupe(rows) {
  const seen = new Set();
  const out = [];
  for (const row of rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    if (seen.has(row.mint)) continue;
    seen.add(row.mint);
    out.push(row);
  }
  return out;
}

async function loadEvents(mints) {
  const byMint = new Map();
  let scanned = 0;
  let used = 0;
  let withReserves = 0;
  for await (const row of readJsonl(capsulesFile)) {
    scanned += 1;
    const mint = rowMint(row);
    if (!mint || !mints.has(mint)) continue;
    const atMs = rowTime(row);
    const mcUsd = num(row.canonical_state?.market_cap_usd) ?? num(row.candidate?.market_cap_usd);
    if (!Number.isFinite(atMs) || !Number.isFinite(mcUsd) || mcUsd <= 0 || mcUsd > maxCapsuleMc) continue;
    const reserves = reservesFromRow(row);
    if (reserves) withReserves += 1;
    const raw = row.raw_record || {};
    const event = {
      mint,
      atMs,
      slot: num(raw.slot) ?? num(row.source_slot) ?? num(row.canonical_state?.source_slot),
      txIndex: num(raw.transactionIndex) ?? num(row.source_transaction_index),
      ixIndex: num(raw.instructionIndex),
      side: raw.side || null,
      wallet: raw.wallet || null,
      quoteLamports: num(raw.quoteLamports),
      sellTokenAmountRaw: raw.sellTokenAmountRaw ? BigInt(raw.sellTokenAmountRaw) : null,
      sellQuoteLamports: num(raw.sellQuoteLamports),
      reserves,
      mcUsd,
      signature: raw.signature || row.transaction_signature || null,
    };
    const arr = byMint.get(mint) || [];
    arr.push(event);
    byMint.set(mint, arr);
    used += 1;
  }
  for (const arr of byMint.values()) {
    arr.sort((a, b) => a.atMs - b.atMs || (a.slot || 0) - (b.slot || 0) || (a.txIndex || 0) - (b.txIndex || 0) || (a.ixIndex || 0) - (b.ixIndex || 0));
  }
  return { byMint, scanned, used, withReserves };
}

function rule(name) {
  const rules = {
    base_buy100_ge_2: (r) => feature(r, "buy_count_100ms") >= 2,
    fast100_fb_le_5000bps: (r) => feature(r, "buy_count_100ms") >= 2 && feature(r, "first_buyer_quote_share_bps") <= 5000,
    fast100_dom_le_050: (r) => feature(r, "buy_count_100ms") >= 2 && feature(r, "dominant_buy_share_60") <= 0.50,
    fast100_mc5000: (r) => feature(r, "buy_count_100ms") >= 2 && feature(r, "entry_mc_usd") <= 5000,
  };
  return { name, test: rules[name] };
}

function split70(rows) {
  const sorted = rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs);
  const cut = Math.floor(sorted.length * 0.7);
  return { train: sorted.slice(0, cut), validation: sorted.slice(cut) };
}

function stateAtOrAfter(events, targetMs, fallbackState) {
  const found = events.find((e) => e.atMs >= targetMs && e.reserves);
  if (found) return { state: { ...found.reserves }, atMs: found.atMs, source: "event_state_at_or_after" };
  const before = events.slice().reverse().find((e) => e.atMs <= targetMs && e.reserves);
  if (before) return { state: { ...before.reserves }, atMs: before.atMs, source: "event_state_before_stale" };
  if (fallbackState) return { state: { ...fallbackState }, atMs: targetMs, source: "entry_input_state" };
  return null;
}

function simulate(row, events, opts = {}) {
  const sizeLamports = Math.floor((opts.sizeSol ?? 0.001) * 1e9);
  const entryDelayMs = opts.entryDelayMs ?? 0;
  const exitDelayMs = opts.exitDelayMs ?? 0;
  const tp = opts.tpPct ?? tpPct;
  const sl = opts.slPct ?? slPct;
  const maxHold = opts.holdMs ?? holdMs;
  const executionFee = opts.executionFeeLamports ?? defaultExecutionFeeLamports;
  const targetEntry = row.entryAtMs + entryDelayMs;
  const start = stateAtOrAfter(events, targetEntry, row.entryReserves);
  if (!start?.state) return { ok: false, reason: "missing_entry_curve_state" };
  const buy = quoteBuyTokens(start.state, sizeLamports);
  if (!buy) return { ok: false, reason: "buy_quote_failed" };
  let state = buy.post;
  const tokens = buy.tokensOut;
  const costLamports = sizeLamports + executionFee;
  const entryImpactPct = Number(buy.curveSol) / Number(start.state.vSol) * 100;
  const path = events.filter((e) => e.atMs >= start.atMs && e.atMs <= start.atMs + maxHold);
  let terminal = null;
  let firstTp = null;
  let firstSl = null;
  let maxPnl = -Infinity;
  let minPnl = Infinity;
  const timeline = [];
  for (const event of path) {
    state = applyExternalEvent(state, event);
    const quote = quoteSellLamports(state, tokens);
    if (!quote) continue;
    const netOut = Number(quote.netOut) - executionFee;
    const pnl = ((netOut - costLamports) / costLamports) * 100;
    const mark = { atMs: event.atMs, rawAtMs: event.atMs, pnlPct: pnl, netOutLamports: netOut, side: event.side };
    timeline.push(mark);
    maxPnl = Math.max(maxPnl, pnl);
    minPnl = Math.min(minPnl, pnl);
    terminal = mark;
    if (!firstTp && pnl >= tp) firstTp = mark;
    if (!firstSl && pnl <= sl) firstSl = mark;
  }
  if (!terminal) return { ok: false, reason: "missing_future_curve_events", entrySource: start.source };
  function delayed(mark) {
    if (!mark || exitDelayMs <= 0) return mark;
    return timeline.find((m) => m.rawAtMs >= mark.rawAtMs + exitDelayMs) || terminal;
  }
  let chosen = terminal;
  let outcome = "none";
  if (firstTp && (!firstSl || firstTp.atMs <= firstSl.atMs)) {
    chosen = delayed(firstTp);
    outcome = "TP";
  } else if (firstSl) {
    chosen = delayed(firstSl);
    outcome = "SL";
  }
  return {
    ok: true,
    outcome,
    pnlPct: chosen.pnlPct,
    exitMs: chosen.atMs - row.entryAtMs,
    rawExitMs: chosen.rawAtMs - row.entryAtMs,
    maxPnlPct: maxPnl,
    minPnlPct: minPnl,
    entryAtMs: start.atMs,
    entryWaitMs: start.atMs - targetEntry,
    entrySource: start.source,
    sizeLamports,
    costLamports,
    tokensOutRaw: tokens,
    entryImpactPct,
    exitImpactPct: Number(tokens) / Number(state.vTok) * 100,
  };
}

function summarize(sims, sizeSol = 0.001) {
  const ok = sims.filter((s) => s.ok && Number.isFinite(s.pnlPct));
  const pnls = ok.map((s) => s.pnlPct);
  const wins = ok.filter((s) => s.pnlPct > 0);
  const losses = ok.filter((s) => s.pnlPct < 0);
  return {
    candidates: sims.length,
    trades: ok.length,
    coverage: pct(ok.length, sims.length),
    wins: wins.length,
    losses: losses.length,
    winRate: pct(wins.length, ok.length),
    grossProfitPct: sum(pnls.filter((x) => x > 0)),
    grossLossPct: sum(pnls.filter((x) => x < 0)),
    netPct: sum(pnls),
    expectancyPct: mean(pnls),
    expectancySol: mean(pnls.map((p) => (p / 100) * sizeSol)),
    pf: pf(pnls),
    worstPct: pnls.length ? Math.min(...pnls) : null,
    medianPct: median(pnls),
    drawdownPct: maxDrawdown(ok),
    losingStreak: losingStreak(ok),
    avgHoldMs: mean(ok.map((s) => s.exitMs)),
    medianHoldMs: median(ok.map((s) => s.exitMs)),
    avgEntryImpactPct: mean(ok.map((s) => s.entryImpactPct)),
    avgExitImpactPct: mean(ok.map((s) => s.exitImpactPct)),
  };
}

function maxDrawdown(rows) {
  let equity = 0;
  let high = 0;
  let dd = 0;
  for (const row of rows) {
    equity += num(row.pnlPct, 0);
    high = Math.max(high, equity);
    dd = Math.min(dd, equity - high);
  }
  return dd;
}

function losingStreak(rows) {
  let best = 0;
  let cur = 0;
  for (const row of rows) {
    if (row.outcome === "SL" || num(row.pnlPct, 0) < 0) cur += 1;
    else cur = 0;
    best = Math.max(best, cur);
  }
  return best;
}

function simulateSet(rows, eventsByMint, opts) {
  return rows.map((row) => ({ row, ...simulate(row, eventsByMint.get(row.mint) || [], opts) }));
}

function summarizeStrategy(rows, eventsByMint, strategy, opts = {}) {
  const subset = rows.filter(strategy.test);
  return { strategy: strategy.name, ...summarize(simulateSet(subset, eventsByMint, opts), opts.sizeSol ?? 0.001) };
}

function trajectory(row, events) {
  const prior = events.filter((e) => e.atMs <= row.entryAtMs && e.atMs >= row.entryAtMs - 2000);
  const buys = prior.filter((e) => e.side === "buy");
  const firstBuyAt = buys[0]?.atMs ?? row.entryAtMs;
  const burst = prior.filter((e) => e.atMs >= firstBuyAt && e.atMs <= firstBuyAt + 100);
  const burstBuys = burst.filter((e) => e.side === "buy");
  const burstSells = burst.filter((e) => e.side === "sell");
  const wallets = new Set(burstBuys.map((e) => e.wallet).filter(Boolean));
  const quoteByWallet = new Map();
  for (const e of burstBuys) quoteByWallet.set(e.wallet || "unknown", (quoteByWallet.get(e.wallet || "unknown") || 0) + (e.quoteLamports || 0));
  const quotes = [...quoteByWallet.values()];
  const totalQuote = sum(quotes);
  const topShare = totalQuote > 0 ? Math.max(...quotes) / totalQuote : null;
  const intervals = [];
  for (let i = 1; i < burstBuys.length; i++) intervals.push(burstBuys[i].atMs - burstBuys[i - 1].atMs);
  let cls = "unknown";
  if (burstSells.length > 0) cls = "buy_sell_mixed";
  else if (wallets.size >= 2 && topShare != null && topShare <= 0.60) cls = "distributed_burst";
  else if (wallets.size === 1 && burstBuys.length >= 2) cls = "repeat_wallet_burst";
  else if (topShare != null && topShare > 0.75) cls = "whale_led_burst";
  else if (wallets.size >= 2) cls = "organic_acceleration";
  return {
    class: cls,
    firstBuyAt,
    burstBuys: burstBuys.length,
    burstSells: burstSells.length,
    uniqueBuyers: wallets.size,
    topWalletShare: topShare,
    medianInterBuyMs: median(intervals),
    totalQuoteSol: totalQuote / 1e9,
  };
}

function groupSummaries(rows, keyFn, eventsByMint, opts) {
  const groups = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    const arr = groups.get(key) || [];
    arr.push(row);
    groups.set(key, arr);
  }
  return [...groups.entries()].map(([key, group]) => ({ group: key, ...summarize(simulateSet(group, eventsByMint, opts), opts.sizeSol ?? 0.001) }))
    .sort((a, b) => b.trades - a.trades);
}

function latencyDistributionFromAudit() {
  const rows = fs.existsSync(auditFile) ? fs.readFileSync(auditFile, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
    try {
      return [JSON.parse(line)];
    } catch {
      return [];
    }
  }) : [];
  const buys = rows.filter((r) => r.kind === "rustSelectedCandidateBuyResult" && Number.isFinite(num(r.selectedToBuyResultMs)));
  const exits = rows.filter((r) => r.kind === "rustExitSellBroadcast" && Number.isFinite(rowTime(r)) && Number.isFinite(num(r.decidedAtMs)));
  return {
    buyCount: buys.length,
    buyMedian: median(buys.map((r) => num(r.selectedToBuyResultMs))),
    buyP75: quantile(buys.map((r) => num(r.selectedToBuyResultMs)), 0.75),
    buyP90: quantile(buys.map((r) => num(r.selectedToBuyResultMs)), 0.9),
    exitCount: exits.length,
    exitMedian: median(exits.map((r) => rowTime(r) - num(r.decidedAtMs))),
  };
}

function capacity(rows, eventsByMint, strategy, maxOpen) {
  const sims = simulateSet(rows.filter(strategy.test), eventsByMint, { sizeSol: 0.001 }).filter((s) => s.ok);
  const open = [];
  const taken = [];
  const blocked = [];
  let peakOpen = 0;
  let occupiedMs = 0;
  for (const sim of sims.sort((a, b) => a.row.entryAtMs - b.row.entryAtMs)) {
    while (open.length && open[0].exitAt <= sim.row.entryAtMs) open.shift();
    if (open.length >= maxOpen) {
      blocked.push(sim);
      continue;
    }
    taken.push(sim);
    const heldMs = Math.min(sim.exitMs || holdMs, holdMs);
    occupiedMs += heldMs;
    open.push({ exitAt: sim.row.entryAtMs + heldMs });
    open.sort((a, b) => a.exitAt - b.exitAt);
    peakOpen = Math.max(peakOpen, open.length);
  }
  const firstAt = sims[0]?.row?.entryAtMs;
  const lastAt = sims.at(-1)?.row?.entryAtMs;
  const capacityMs = Number.isFinite(firstAt) && Number.isFinite(lastAt) && lastAt > firstAt
    ? (lastAt - firstAt + holdMs) * maxOpen
    : null;
  const summary = summarize(taken, 0.001);
  return {
    name: `max_open_${maxOpen}`,
    maxOpen,
    eligibleSignals: sims.length,
    tradesTaken: taken.length,
    winnersBlocked: blocked.filter((s) => s.pnlPct > 0).length,
    losersBlocked: blocked.filter((s) => s.pnlPct < 0).length,
    blocked: blocked.length,
    peakOpen,
    utilizationPct: capacityMs ? (occupiedMs / capacityMs) * 100 : null,
    ...summary,
    candidates: sims.length,
    trades: taken.length,
  };
}

function walkForward(rows, eventsByMint) {
  const sorted = rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs);
  const size = Math.floor(sorted.length / 5);
  const candidates = [rule("fast100_fb_le_5000bps"), rule("fast100_dom_le_050"), rule("fast100_mc5000"), rule("base_buy100_ge_2")];
  const folds = [];
  for (let i = 1; i < 5; i++) {
    const train = sorted.slice(0, i * size);
    const test = sorted.slice(i * size, (i + 1) * size);
    const ranked = candidates.map((r) => summarizeStrategy(train, eventsByMint, r)).filter((r) => r.trades >= minReportRows).sort((a, b) => b.pf - a.pf || b.expectancyPct - a.expectancyPct);
    const chosen = candidates.find((r) => r.name === ranked[0]?.strategy);
    const result = chosen ? summarizeStrategy(test, eventsByMint, chosen) : { trades: 0 };
    folds.push({ fold: i, chosen: chosen?.name || "none", start: test[0]?.entryAtMs, end: test.at(-1)?.entryAtMs, ...result });
  }
  return folds;
}

function walkForwardCompare(rows, eventsByMint, strategies) {
  const sorted = rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs);
  const size = Math.floor(sorted.length / 5);
  const out = [];
  for (let i = 1; i < 5; i++) {
    const test = sorted.slice(i * size, (i + 1) * size);
    for (const strategy of strategies) {
      out.push({
        fold: i,
        strategy: strategy.name,
        start: test[0]?.entryAtMs,
        end: test.at(-1)?.entryAtMs,
        ...summarizeStrategy(test, eventsByMint, strategy),
      });
    }
  }
  return out;
}

async function loadProspectiveSelected(eventsByMint) {
  const selected = [];
  for await (const row of readJsonl(selectedFile)) {
    if (rowTime(row) < sinceMs) continue;
    const features = {
      entry_mc_usd: num(row.entryMarketCapUsd) ?? num(row.features?.marketCapUsd),
      buy_count_100ms: num(row.features?.buyCount100ms),
      dominant_buy_share_60: num(row.features?.dominantBuyShare60),
      first_buyer_quote_share_bps: num(row.features?.firstBuyerQuoteShareBps),
      position_size_lamports: num(row.directEntryQuote?.solInLamports, 1_000_000),
    };
    const snap = row.directEntryQuote?.curveSnapshot;
    const reserves = snap?.virtualSolReserves && snap?.virtualTokenReserves
      ? { vSol: BigInt(snap.virtualSolReserves), vTok: BigInt(snap.virtualTokenReserves) }
      : null;
    selected.push({
      mint: row.mint,
      entryAtMs: rowTime(row),
      features,
      entryReserves: reserves,
      source: "live_selected_events",
    });
  }
  const qualified = dedupe(selected).filter(rule("fast100_fb_le_5000bps").test);
  return { selected: selected.length, qualified, sims: simulateSet(qualified, eventsByMint, { sizeSol: 0.001 }) };
}

function printStats(title, rows) {
  console.log(`## ${title}`);
  console.log("");
  console.log(markdownTable(rows, [
    { label: "Name", value: (r) => r.strategy || r.group || r.name || r.size || `${r.delayMs}ms` || r.maxOpen },
    { label: "Candidates", value: (r) => r.candidates ?? r.eligibleSignals ?? "" },
    { label: "Trades", value: (r) => r.trades ?? r.tradesTaken },
    { label: "Coverage", value: (r) => fmtPct(r.coverage) },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Exp %", value: (r) => fmtPct(r.expectancyPct) },
    { label: "Exp SOL", value: (r) => fmtSol(r.expectancySol) },
    { label: "Worst", value: (r) => fmtPct(r.worstPct) },
    { label: "DD", value: (r) => fmtPct(r.drawdownPct) },
    { label: "Avg entry impact", value: (r) => fmtPct(r.avgEntryImpactPct) },
    { label: "Avg exit impact", value: (r) => fmtPct(r.avgExitImpactPct) },
  ]));
  console.log("");
}

async function main() {
  const receiptLoad = await loadReceipts();
  const inputLoad = await loadInputs(receiptLoad.inputHashes);
  const rows = buildRows(receiptLoad.receipts, inputLoad.inputs);
  const eventsLoad = await loadEvents(new Set(rows.map((r) => r.mint)));
  const split = split70(rows);
  const leaderA = rule("fast100_fb_le_5000bps");
  const leaderB = rule("fast100_dom_le_050");
  const base = rule("base_buy100_ge_2");
  const liveLatency = latencyDistributionFromAudit();

  const coreRows = [base, leaderA, leaderB, rule("fast100_mc5000")].map((r) => summarizeStrategy(split.validation, eventsLoad.byMint, r));
  const trajRows = split.validation.filter(base.test).map((row) => ({ row, ...trajectory(row, eventsLoad.byMint.get(row.mint) || []) }));
  const trajectoryGroups = groupSummaries(trajRows.map((x) => ({ ...x.row, _trajectory: x })), (r) => r._trajectory.class, eventsLoad.byMint, { sizeSol: 0.001 });
  const sizeRows = sizeSols.map((sizeSol) => ({ name: `${sizeSol} SOL`, ...summarize(simulateSet(split.validation.filter(leaderA.test), eventsLoad.byMint, { sizeSol }), sizeSol) }));
  const entryLatencyRows = entryDelaysMs.map((delayMs) => ({ name: `${delayMs}ms`, delayMs, ...summarize(simulateSet(split.validation.filter(leaderA.test), eventsLoad.byMint, { sizeSol: 0.001, entryDelayMs: delayMs }), 0.001) }));
  const actualDelay = Math.round(liveLatency.buyMedian || 150);
  entryLatencyRows.push({ name: `actual_median_${actualDelay}ms`, delayMs: actualDelay, ...summarize(simulateSet(split.validation.filter(leaderA.test), eventsLoad.byMint, { sizeSol: 0.001, entryDelayMs: actualDelay }), 0.001) });
  const exitLatencyRows = exitDelaysMs.map((delayMs) => ({ name: `${delayMs}ms`, delayMs, ...summarize(simulateSet(split.validation.filter(leaderA.test), eventsLoad.byMint, { sizeSol: 0.001, exitDelayMs: delayMs }), 0.001) }));
  const tpHoldRows = [];
  for (const tp of tpGrid) for (const sl of slGrid) for (const h of holdGrid) {
    const train = summarize(simulateSet(split.train.filter(leaderA.test), eventsLoad.byMint, { sizeSol: 0.001, tpPct: tp, slPct: sl, holdMs: h }), 0.001);
    const val = summarize(simulateSet(split.validation.filter(leaderA.test), eventsLoad.byMint, { sizeSol: 0.001, tpPct: tp, slPct: sl, holdMs: h }), 0.001);
    if (val.trades >= minReportRows) tpHoldRows.push({ name: `tp${tp}_sl${Math.abs(sl)}_${h / 1000}s`, tp, sl, hold: h, trainPf: train.pf, ...val });
  }
  const capacityRows = maxOpenValues.map((n) => ({ name: `max_open_${n}`, ...capacity(split.validation, eventsLoad.byMint, leaderA, n) }));
  const wf = walkForward(rows, eventsLoad.byMint);
  const wfCompare = walkForwardCompare(rows, eventsLoad.byMint, [leaderA, leaderB]);
  const prospective = await loadProspectiveSelected(eventsLoad.byMint);

  console.log("# High-Fidelity Execution Replay + Prospective Benchmark Validation");
  console.log("");
  console.log("Read-only analytics only. No live strategy/config/restart/deployment changes.");
  console.log("");
  console.log("## Data Coverage");
  console.log("");
  console.log(markdownTable([
    { metric: "Accepted receipts", value: receiptLoad.receipts.length },
    { metric: "Unique deduped mints", value: rows.length },
    { metric: "Decision inputs scanned", value: inputLoad.scanned },
    { metric: "Hydrated inputs", value: inputLoad.inputs.size },
    { metric: "Capsules scanned", value: eventsLoad.scanned },
    { metric: "Usable event rows", value: eventsLoad.used },
    { metric: "Event rows with reserves", value: eventsLoad.withReserves },
    { metric: "Observed live buy samples", value: liveLatency.buyCount },
    { metric: "Observed selected->buy median", value: `${fmt(liveLatency.buyMedian, 1)}ms` },
    { metric: "Observed selected->buy P90", value: `${fmt(liveLatency.buyP90, 1)}ms` },
  ], [
    { label: "Metric", value: (r) => r.metric },
    { label: "Value", value: (r) => r.value },
  ]));
  console.log("");
  console.log("Replay method: parse virtual reserves from `canonical_state.state_identity`, simulate exact-SOL Pump buy with fee-aware curve input, then replay subsequent raw buy/sell events into a counterfactual curve and quote our sell from that curve. Rows without enough curve/tape data are excluded from high-fidelity headline trades.");
  console.log("");

  printStats("1. High-Fidelity Replay Results", coreRows);
  printStats("2. Microburst Trajectory Analysis", trajectoryGroups);

  console.log("## 3. Distributed Vs Whale-Led Burst Detail");
  console.log("");
  console.log(markdownTable([...new Map(trajRows.map((r) => [r.class, r])).values()].map((r) => ({
    class: r.class,
    examples: trajRows.filter((x) => x.class === r.class).slice(0, 5).map((x) => `\`${x.row.mint}\``).join(", "),
    count: trajRows.filter((x) => x.class === r.class).length,
    medianBuySpacing: median(trajRows.filter((x) => x.class === r.class).map((x) => x.medianInterBuyMs)),
    medianTopShare: median(trajRows.filter((x) => x.class === r.class).map((x) => x.topWalletShare)),
  })), [
    { label: "Class", value: (r) => r.class },
    { label: "Count", value: (r) => r.count },
    { label: "Median inter-buy", value: (r) => r.medianBuySpacing == null ? "n/a" : `${fmt(r.medianBuySpacing, 1)}ms` },
    { label: "Median top share", value: (r) => fmtPct((r.medianTopShare ?? 0) * 100) },
    { label: "Examples", value: (r) => r.examples },
  ]));
  console.log("");

  printStats("4. Nonlinear Size Sensitivity - fast100_fb_le_5000bps", sizeRows);
  printStats("5. Entry Latency Sensitivity - fast100_fb_le_5000bps", entryLatencyRows);
  printStats("6. Exit Latency Sensitivity - fast100_fb_le_5000bps", exitLatencyRows);

  console.log("## 7. High-Fidelity TP/SL/Hold OOS");
  console.log("");
  console.log(markdownTable(tpHoldRows.sort((a, b) => b.pf - a.pf || b.expectancyPct - a.expectancyPct).slice(0, 20), [
    { label: "Policy", value: (r) => r.name },
    { label: "Train PF", value: (r) => Number.isFinite(r.trainPf) ? fmt(r.trainPf, 2) : "inf" },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Exp", value: (r) => fmtPct(r.expectancyPct) },
    { label: "DD", value: (r) => fmtPct(r.drawdownPct) },
  ]));
  console.log("");

  console.log("## 8. MAX_OPEN 1/2/3/4 - fast100_fb_le_5000bps");
  console.log("");
  console.log(markdownTable(capacityRows, [
    { label: "Max open", value: (r) => r.name },
    { label: "Eligible", value: (r) => r.eligibleSignals },
    { label: "Taken", value: (r) => r.tradesTaken },
    { label: "Blocked", value: (r) => r.blocked },
    { label: "Winners blocked", value: (r) => r.winnersBlocked },
    { label: "Losers blocked", value: (r) => r.losersBlocked },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Exp", value: (r) => fmtPct(r.expectancyPct) },
    { label: "Net %", value: (r) => fmtPct(r.netPct) },
    { label: "DD", value: (r) => fmtPct(r.drawdownPct) },
    { label: "Peak exposure", value: (r) => r.peakOpen },
    { label: "Utilization", value: (r) => fmtPct(r.utilizationPct) },
  ]));
  console.log("");
  printStats("9. FAST100_FB VS FAST100_DOM", [summarizeStrategy(split.validation, eventsLoad.byMint, leaderA), summarizeStrategy(split.validation, eventsLoad.byMint, leaderB)]);
  console.log("Walk-forward high-fidelity comparison:");
  console.log("");
  console.log(markdownTable(wfCompare, [
    { label: "Fold", value: (r) => r.fold },
    { label: "Strategy", value: (r) => r.strategy },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Exp", value: (r) => fmtPct(r.expectancyPct) },
    { label: "DD", value: (r) => fmtPct(r.drawdownPct) },
  ]));
  console.log("");

  console.log("## 10. Prospective Live-Shadow Implementation");
  console.log("");
  console.log("Implemented as read-only derived telemetry from existing `live-mc5000-selected-events.jsonl` plus capsule/audit streams. It does not alter live buy eligibility and was not deployed as a live service hook because this phase forbids restarts/config/live behavior changes.");
  console.log("");
  printStats("11. Prospective Sample Results If Available", [{ name: "prospective_fast100_fb_le_5000bps", ...summarize(prospective.sims, 0.001), candidates: prospective.qualified.length }]);

  console.log("## 12. Historical Vs Prospective Comparison");
  console.log("");
  console.log(markdownTable([
    { name: "historical_oos_fast100_fb", ...summarizeStrategy(split.validation, eventsLoad.byMint, leaderA) },
    { name: "prospective_live_selected_fast100_fb", ...summarize(prospective.sims, 0.001), candidates: prospective.qualified.length },
  ], [
    { label: "Set", value: (r) => r.name || r.strategy },
    { label: "Candidates", value: (r) => r.candidates },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Exp", value: (r) => fmtPct(r.expectancyPct) },
    { label: "Coverage", value: (r) => fmtPct(r.coverage) },
  ]));
  console.log("");

  console.log("## 13. Benchmark Confidence Status");
  console.log("");
  const hist = summarizeStrategy(split.validation, eventsLoad.byMint, leaderA);
  const stages = [
    { stage: "RESEARCH-PROMISING", status: hist.trades >= 200 && hist.pf > 1.2 ? "PASS" : "FAIL", remaining: "Continue checking threshold/period stability." },
    { stage: "HISTORICALLY-VALIDATED", status: wf.filter((f) => f.pf > 1).length >= 3 ? "PASS" : "FAIL", remaining: "All folds should remain positive after any method changes." },
    { stage: "HIGH-FIDELITY-VALIDATED", status: hist.trades >= 200 && hist.pf > 1.2 ? "PROVISIONAL PASS" : "FAIL", remaining: "Replay is counterfactual from available tape; raw full event tape improves confidence." },
    { stage: "PROSPECTIVELY-VALIDATED", status: prospective.sims.filter((s) => s.ok).length >= 100 ? "PASS" : "PENDING", remaining: "Needs enough live-selected prospective samples." },
    { stage: "DUST-LIVE-READY", status: "PENDING", remaining: "Requires high-fidelity + prospective agreement and no methodology gap." },
    { stage: "CAPITAL-SCALE-READY", status: "NO", remaining: "Needs nonlinear replay confidence at 0.5-1 SOL and dust A/B." },
  ];
  console.log(markdownTable(stages, [
    { label: "Stage", value: (r) => r.stage },
    { label: "Status", value: (r) => r.status },
    { label: "Remaining requirement", value: (r) => r.remaining },
  ]));
  console.log("");

  console.log("## 14. Next Promotion Gate");
  console.log("");
  console.log("Do not deploy. Next gate is a prospective live-shadow file/service for `fast100_fb_le_5000bps` that records qualification and high-fidelity outcome from the live pipeline without changing buy eligibility. After that, compare 100+ prospective outcomes against this historical OOS replay.");
  console.log("");

  console.log("## Direct Answers");
  console.log("");
  console.log(`A. Edge survives available Pump curve replay? ${hist.trades >= 200 && hist.pf > 1 ? "Yes, provisionally" : "Not proven"}: OOS trades=${hist.trades}, PF=${fmt(hist.pf, 2)}, expectancy=${fmtPct(hist.expectancyPct)}.`);
  const actual = entryLatencyRows.find((r) => r.name.startsWith("actual_median"));
  console.log(`B. Survives actual entry latency? ${actual?.pf > 1 ? "Provisionally yes" : "Not proven"} at median ${actualDelay}ms: PF=${fmt(actual?.pf, 2)}, expectancy=${fmtPct(actual?.expectancyPct)}.`);
  const damaged = sizeRows.find((r) => r.pf < hist.pf * 0.8 || r.expectancyPct < hist.expectancyPct * 0.8);
  console.log(`C. Size damage point: ${damaged?.name || "not observed in this replay"}, but 0.5-1 SOL remains counterfactual and needs raw path verification.`);
  const distributed = trajectoryGroups.find((r) => r.group === "distributed_burst");
  const repeat = trajectoryGroups.find((r) => r.group === "repeat_wallet_burst");
  console.log(`D. Microburst distribution: distributed trades=${distributed?.trades || 0}, repeat-wallet trades=${repeat?.trades || 0}; see section 2/3.`);
  console.log("E. first-buyer-share vs dominance: section 9 compares A/B under high-fidelity replay; favor A if similar PF with larger sample.");
  const bestCap = capacityRows.slice().sort((a, b) => b.pf - a.pf || b.expectancyPct - a.expectancyPct)[0];
  console.log(`F. MAX_OPEN: current high-fidelity best by PF is ${bestCap?.name}, PF=${fmt(bestCap?.pf, 2)}, trades=${bestCap?.trades}.`);
  const pros = summarize(prospective.sims, 0.001);
  console.log(`G. Prospective confirmation: ${pros.trades >= 100 ? "enough sample" : "not enough sample yet"}; trades=${pros.trades}, PF=${fmt(pros.pf, 2)}, expectancy=${fmtPct(pros.expectancyPct)}.`);
  console.log(`H. Dust live A/B ready? ${hist.pf > 1.2 && pros.trades >= 100 && pros.pf > 1.1 ? "YES for dust only" : "NO, keep prospective validation first"}.`);
}

main().catch((err) => {
  console.error(err?.stack || err);
  process.exit(1);
});
