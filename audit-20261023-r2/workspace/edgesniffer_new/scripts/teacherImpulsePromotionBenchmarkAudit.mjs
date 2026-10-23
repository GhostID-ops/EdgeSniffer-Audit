#!/usr/bin/env node
import fs from "node:fs";
import readline from "node:readline";
import zlib from "node:zlib";
import crypto from "node:crypto";

const baseDir = process.env.TEACHER_IMPULSE_BASE_DIR
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1";
const inputsFile = process.env.TEACHER_IMPULSE_INPUTS_FILE || `${baseDir}/decision-inputs.jsonl`;
const capsulesFile = process.env.TEACHER_IMPULSE_CAPSULES_FILE || `${baseDir}/candidate-capsules.jsonl`;
const selectedFile = process.env.TEACHER_IMPULSE_SELECTED_EVENT_OUTPUT || `${baseDir}/live-mc5000-selected-events.jsonl`;
const auditFile = process.env.RUST_SELECTED_CANDIDATE_AUDIT_FILE || `${baseDir}/live-mc5000-dust/rust-selected-candidate-audit.jsonl`;
const receiptLaneSpecs = (process.env.RECEIPT_LANES || [
  `parent=${baseDir}/decision-receipts.jsonl`,
  `mc5000=${baseDir}/children/mc5000/decision-receipts.jsonl`,
  `mc5000_dom55=${baseDir}/children/mc5000-dom55/decision-receipts.jsonl`,
  `mc5000_fb5000=${baseDir}/children/mc5000-fb5000/decision-receipts.jsonl`,
].join(",")).split(",").filter(Boolean);

const sinceMs = process.env.REPORT_SINCE_MS ? Number(process.env.REPORT_SINCE_MS) : Date.parse(process.env.REPORT_SINCE || "2026-08-29T23:06:27Z");
const selectedSinceMs = process.env.LIVE_REPORT_SINCE_MS ? Number(process.env.LIVE_REPORT_SINCE_MS) : Date.parse(process.env.LIVE_REPORT_SINCE || "2026-08-29T23:06:27Z");
const primaryTp = Number(process.env.PRIMARY_TP_PCT || 20);
const primarySl = Number(process.env.PRIMARY_SL_PCT || -20);
const primaryHoldMs = Number(process.env.PRIMARY_HOLD_MS || 120000);
const roundTripCostBps = Number(process.env.ROUND_TRIP_COST_BPS || 250);
const maxCapsuleMc = Number(process.env.MAX_CAPSULE_MC_USD || 500000);
const minRows = Number(process.env.MIN_PROMOTION_SAMPLE || 25);

const windows = [50, 100, 150, 200, 250, 300, 400, 500, 750, 1000];
const counts = [2, 3, 4, 5, 6];
const mcCaps = [3000, 4000, 5000, 6000, 7500];
const maxOpenValues = [1, 2, 3, 4];
const tpList = [10, 15, 20, 25, 30];
const slList = [-10, -15, -20, -25];
const holdList = [15000, 30000, 45000, 60000, 90000, 120000];

function parseLaneSpec(spec) {
  const idx = spec.indexOf("=");
  if (idx < 0) throw new Error(`bad lane spec ${spec}`);
  return { name: spec.slice(0, idx), file: spec.slice(idx + 1) };
}

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
    ?? num(row?.provider?.candidate_ready_ts_ms)
    ?? num(row?.candidate_ready_ts_ms)
    ?? num(row?.observedAtMs)
    ?? num(row?.canonical_state?.observed_at_ms)
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

function fmtSolPct(value) {
  return Number.isFinite(value) ? fmt(value / 100, 5) : null;
}

function pct(count, total) {
  return total ? (count / total) * 100 : 0;
}

function sum(values) {
  return values.filter(Number.isFinite).reduce((acc, value) => acc + value, 0);
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function mean(values) {
  const xs = values.filter(Number.isFinite);
  return xs.length ? sum(xs) / xs.length : null;
}

function quantile(values, q) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const idx = Math.min(xs.length - 1, Math.max(0, Math.floor((xs.length - 1) * q)));
  return xs[idx];
}

function stddev(values) {
  const xs = values.filter(Number.isFinite);
  if (xs.length < 2) return null;
  const m = mean(xs);
  return Math.sqrt(sum(xs.map((x) => (x - m) ** 2)) / (xs.length - 1));
}

function pf(pnls) {
  const gp = sum(pnls.filter((x) => x > 0));
  const gl = Math.abs(sum(pnls.filter((x) => x < 0)));
  return gl > 0 ? gp / gl : gp > 0 ? Infinity : 0;
}

function markdownTable(rows, columns) {
  if (!rows.length) return "No rows.";
  return [
    `| ${columns.map((col) => col.label).join(" | ")} |`,
    `| ${columns.map(() => "---").join(" | ")} |`,
    ...rows.map((row) => `| ${columns.map((col) => String(col.value(row) ?? "").replaceAll("|", "/")).join(" | ")} |`),
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
      // best-effort analytics
    }
  }
}

function hydrateFeatures(input, receipt) {
  const ready = num(get(input, "provider.candidate_ready_ts_ms")) ?? rowTime(input);
  const stateAt = num(get(input, "canonical_state.observed_at_ms"));
  const c = input.candidate || {};
  const e = input.execution || {};
  const tokenProgram = String(c.token_program || "");
  const f = {
    entry_mc_usd: num(receipt.calculated_market_cap_usd) ?? num(c.market_cap_usd) ?? num(get(input, "canonical_state.market_cap_usd")),
    creation_age_ms: num(c.creation_age_ms),
    net_buy_sol_60: num(c.net_buy_sol_60),
    buy_count_50ms: num(c.buy_count_50ms),
    buy_count_100ms: num(c.buy_count_100ms),
    buy_count_150ms: num(c.buy_count_150ms),
    buy_count_200ms: num(c.buy_count_200ms),
    buy_count_250ms: num(c.buy_count_250ms),
    buy_count_300ms: num(c.buy_count_300ms),
    buy_count_400ms: num(c.buy_count_400ms),
    buy_count_500ms: num(c.buy_count_500ms),
    buy_count_750ms: num(c.buy_count_750ms),
    buy_count_1000ms: num(c.buy_count_1000ms),
    buy_count_60: num(c.buy_count_60),
    sell_pressure_60: num(c.sell_pressure_60),
    dominant_buy_share_60: num(c.dominant_buy_share_60),
    unique_wallets_60: num(c.unique_wallets_60),
    unique_per_buy_60: num(c.unique_per_buy_60),
    first_buyer_quote_share_bps: num(c.first_buyer_quote_share_bps),
    largest_wallet_quote_share_bps: num(c.largest_wallet_quote_share_bps),
    expected_tokens_out_raw: num(e.expected_tokens_out_raw),
    min_tokens_out_raw: num(e.min_tokens_out_raw),
    position_size_lamports: num(e.position_size_lamports),
    priority_fee_lamports: num(e.priority_fee_lamports),
    jito_tip_lamports: num(e.jito_tip_lamports),
    state_age_ms: stateAt != null && ready != null ? ready - stateAt : null,
    token_program_2022: tokenProgram.includes("Tokenz") ? 1 : 0,
    has_source_ix_data: c.source_ix_data_base64 ? 1 : 0,
    has_source_ix_accounts: Array.isArray(get(input, "extra_decision_fields.sourceIxAccounts")) ? 1 : 0,
  };
  f.velocity_100_to_1000 = f.buy_count_1000ms > 0 ? f.buy_count_100ms / f.buy_count_1000ms : null;
  f.accel_1000_minus_500 = f.buy_count_1000ms != null && f.buy_count_500ms != null ? f.buy_count_1000ms - f.buy_count_500ms : null;
  return f;
}

function scoreMcMarks(entryMc, entryAtMs, marks, tp = primaryTp, sl = primarySl, holdMs = primaryHoldMs) {
  if (!Number.isFinite(entryMc) || entryMc <= 0 || !marks?.length) return missingScore();
  let firstTp = null;
  let firstSl = null;
  let terminal = null;
  let maxRoi = -Infinity;
  let minRoi = Infinity;
  const costPct = roundTripCostBps / 100;
  for (const mark of marks) {
    const dt = mark.tsMs - entryAtMs;
    if (dt < 0 || dt > holdMs) continue;
    const roi = ((mark.mcUsd / entryMc) - 1) * 100 - costPct;
    maxRoi = Math.max(maxRoi, roi);
    minRoi = Math.min(minRoi, roi);
    terminal = { roi, dt };
    if (!firstTp && roi >= tp) firstTp = { roi, dt };
    if (!firstSl && roi <= sl) firstSl = { roi, dt };
  }
  return scoreFromEvents(firstTp, firstSl, terminal, maxRoi, minRoi, tp, sl, holdMs);
}

function scorePnlMarks(entryAtMs, marks, tp = primaryTp, sl = primarySl, holdMs = primaryHoldMs) {
  const usable = (marks || []).filter((m) => Number.isFinite(m.tsMs) && Number.isFinite(m.pnlPct)).sort((a, b) => a.tsMs - b.tsMs);
  if (!Number.isFinite(entryAtMs) || !usable.length) return missingScore();
  let firstTp = null;
  let firstSl = null;
  let terminal = null;
  let maxRoi = -Infinity;
  let minRoi = Infinity;
  for (const mark of usable) {
    const dt = mark.tsMs - entryAtMs;
    if (dt < 0 || dt > holdMs) continue;
    const roi = mark.pnlPct;
    maxRoi = Math.max(maxRoi, roi);
    minRoi = Math.min(minRoi, roi);
    terminal = { roi, dt };
    if (!firstTp && roi >= tp) firstTp = { roi, dt };
    if (!firstSl && roi <= sl) firstSl = { roi, dt };
  }
  return scoreFromEvents(firstTp, firstSl, terminal, maxRoi, minRoi, tp, sl, holdMs);
}

function missingScore() {
  return { outcome: "missing", pnlPct: null, exitMs: null, maxRoiPct: null, minRoiPct: null };
}

function scoreFromEvents(firstTp, firstSl, terminal, maxRoi, minRoi, tp, sl, holdMs) {
  if (firstTp && (!firstSl || firstTp.dt <= firstSl.dt)) {
    return { outcome: "TP", pnlPct: tp, exitMs: firstTp.dt, maxRoiPct: maxRoi, minRoiPct: minRoi };
  }
  if (firstSl) return { outcome: "SL", pnlPct: sl, exitMs: firstSl.dt, maxRoiPct: maxRoi, minRoiPct: minRoi };
  return {
    outcome: "none",
    pnlPct: terminal ? terminal.roi : null,
    exitMs: terminal ? terminal.dt : holdMs,
    maxRoiPct: Number.isFinite(maxRoi) ? maxRoi : null,
    minRoiPct: Number.isFinite(minRoi) ? minRoi : null,
  };
}

function liveDecisionPnl(row) {
  return num(row?.feeAdjustedPnlPct) ?? num(row?.pnlPct) ?? num(row?.grossPnlPct);
}

function labelFromLiveDecision(row) {
  if (!row) return null;
  const pnlPct = liveDecisionPnl(row);
  const reason = String(row.reason || row.exitReason || "").toLowerCase();
  let outcome = "none";
  if (reason.includes("take-profit") || reason === "tp") outcome = "TP";
  else if (reason.includes("stop") || reason.includes("hard") || reason === "sl") outcome = "SL";
  else if (Number.isFinite(pnlPct) && pnlPct >= primaryTp) outcome = "TP";
  else if (Number.isFinite(pnlPct) && pnlPct <= primarySl) outcome = "SL";
  return { outcome, pnlPct, reason: row.reason || row.exitReason || null };
}

async function loadReceiptLanes() {
  const lanes = new Map();
  const inputHashes = new Set();
  for (const specRaw of receiptLaneSpecs) {
    const spec = parseLaneSpec(specRaw);
    const lane = { spec, receipts: [], statusCounts: new Map(), duplicateHashes: 0 };
    const seenHashes = new Set();
    for await (const row of readJsonl(spec.file)) {
      if (rowTime(row) < sinceMs) continue;
      lane.statusCounts.set(row.status || "missing", (lane.statusCounts.get(row.status || "missing") || 0) + 1);
      if (row.status !== "accepted" || !row.input_hash || !row.mint) continue;
      if (seenHashes.has(row.input_hash)) lane.duplicateHashes += 1;
      seenHashes.add(row.input_hash);
      lane.receipts.push(row);
      inputHashes.add(row.input_hash);
    }
    lanes.set(spec.name, lane);
  }
  return { lanes, inputHashes };
}

async function loadInputs(inputHashes) {
  const inputs = new Map();
  let total = 0;
  for await (const row of readJsonl(inputsFile)) {
    total += 1;
    const hash = crypto.createHash("sha256").update(row.__rawLine || JSON.stringify(row)).digest("hex");
    if (hash && inputHashes.has(hash) && !inputs.has(hash)) inputs.set(hash, row);
  }
  return { inputs, total };
}

async function loadCapsuleMarks(mints) {
  const marks = new Map();
  let total = 0;
  let used = 0;
  for await (const row of readJsonl(capsulesFile)) {
    total += 1;
    const mint = rowMint(row);
    if (!mint || !mints.has(mint)) continue;
    const tsMs = rowTime(row);
    const mcUsd = num(row.canonical_state?.market_cap_usd) ?? num(row.candidate?.market_cap_usd);
    if (!Number.isFinite(tsMs) || !Number.isFinite(mcUsd) || mcUsd <= 0 || mcUsd > maxCapsuleMc) continue;
    const arr = marks.get(mint) || [];
    arr.push({ tsMs, mcUsd });
    marks.set(mint, arr);
    used += 1;
  }
  for (const arr of marks.values()) arr.sort((a, b) => a.tsMs - b.tsMs);
  return { marks, total, used };
}

function readJsonlSync(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean).flatMap((line) => {
    try {
      return [JSON.parse(line)];
    } catch {
      return [];
    }
  });
}

function loadLiveAuditModels() {
  const selected = readJsonlSync(selectedFile).filter((row) => rowTime(row) >= selectedSinceMs);
  const audits = readJsonlSync(auditFile).filter((row) => rowTime(row) >= selectedSinceMs);
  const selectedByMint = new Map();
  for (const row of selected.sort((a, b) => rowTime(a) - rowTime(b))) {
    if (row.mint && !selectedByMint.has(row.mint)) selectedByMint.set(row.mint, row);
  }
  const byMint = new Map();
  for (const row of audits) {
    const mint = rowMint(row);
    if (!mint) continue;
    const s = byMint.get(mint) || { mint, rows: [], buyStart: null, buy: null, finality: null, exitMarks: [], exitDecision: null };
    s.rows.push(row);
    if (row.kind === "rustSelectedCandidateBuyStart" && !s.buyStart) s.buyStart = row;
    if (row.kind === "rustSelectedCandidateBuyResult" && !s.buy) s.buy = row;
    if (row.kind === "rustSelectedCandidateBuyFinality" && !s.finality) s.finality = row;
    if (row.kind === "rustExitMark" && Number.isFinite(liveDecisionPnl(row))) {
      s.exitMarks.push({ tsMs: rowTime(row), pnlPct: liveDecisionPnl(row), markSource: row.markSource || null });
    }
    if (row.kind === "rustExitDecision") s.exitDecision = row;
    byMint.set(mint, s);
  }
  const models = new Map();
  for (const [mint, s] of byMint) {
    s.exitMarks.sort((a, b) => a.tsMs - b.tsMs);
    const entryAtMs = rowTime(s.buyStart) || rowTime(s.buy) || rowTime(selectedByMint.get(mint));
    const markScore = scorePnlMarks(entryAtMs, s.exitMarks);
    models.set(mint, {
      mint,
      selected: selectedByMint.get(mint) || null,
      selectedAtMs: rowTime(selectedByMint.get(mint)),
      buyMs: num(s.buy?.selectedToBuyResultMs),
      finalGuardMc: num(s.buy?.finalEconomicGuardEntryMarketCapUsd),
      guardSource: s.buy?.finalEconomicGuardReserveSource || s.buy?.preSignReserveSource || null,
      exitMarks: s.exitMarks,
      markScore,
      observedLive: labelFromLiveDecision(s.exitDecision),
      exitDecision: s.exitDecision,
      rawRows: s.rows.length,
    });
  }
  return { models, selectedCount: selected.length, auditCount: audits.length };
}

function buildRows(lanes, inputs, capsuleMarks, liveModels) {
  const rows = [];
  for (const [laneName, lane] of lanes) {
    for (const receipt of lane.receipts) {
      const input = inputs.get(receipt.input_hash);
      if (!input) continue;
      const entryAtMs = num(get(input, "provider.candidate_ready_ts_ms")) ?? rowTime(input);
      const features = hydrateFeatures(input, receipt);
      const theoretical = scoreMcMarks(features.entry_mc_usd, entryAtMs, capsuleMarks.get(receipt.mint));
      const live = liveModels.get(receipt.mint);
      const executable = live?.markScore?.outcome && live.markScore.outcome !== "missing" ? live.markScore : theoretical;
      const source = live?.markScore?.outcome && live.markScore.outcome !== "missing" ? "live_watcher_audit_economics" : "capsule_proxy";
      const observed = live?.observedLive || null;
      rows.push({
        lane: laneName,
        mint: receipt.mint,
        inputHash: receipt.input_hash,
        opportunityKey: `${receipt.mint}:${Math.floor(entryAtMs / 250)}`,
        entryAtMs,
        sourceSlot: num(receipt.source_slot),
        features,
        theoreticalOutcome: theoretical.outcome,
        theoreticalPnlPct: theoretical.pnlPct,
        theoreticalExitMs: theoretical.exitMs,
        outcome: executable.outcome,
        pnlPct: executable.pnlPct,
        exitMs: executable.exitMs,
        maxRoiPct: executable.maxRoiPct,
        minRoiPct: executable.minRoiPct,
        labelSource: source,
        observedLiveOutcome: observed?.outcome || null,
        observedLivePnlPct: observed?.pnlPct ?? null,
        observedLiveReason: observed?.reason || null,
        liveBuyMs: live?.buyMs ?? null,
        finalGuardMc: live?.finalGuardMc ?? null,
        guardSource: live?.guardSource || null,
        capsuleMarks: capsuleMarks.get(receipt.mint)?.length || 0,
        liveMarks: live?.exitMarks?.length || 0,
      });
    }
  }
  return rows;
}

function dedupe(rows, mode = "mint") {
  const seen = new Set();
  const out = [];
  for (const row of rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    const key = mode === "opportunity" ? row.opportunityKey : row.mint;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}

function feature(row, name) {
  return num(row.features?.[name]);
}

function buyCount(row, windowMs) {
  return feature(row, `buy_count_${windowMs}ms`);
}

function ruleDefs() {
  return [
    { name: "parent", test: () => true },
    { name: "mc_le_5000", test: (r) => feature(r, "entry_mc_usd") <= 5000 },
    { name: "fast_burst_buy250_ge_3", test: (r) => buyCount(r, 250) >= 3 },
    { name: "fast_burst_buy100_ge_2", test: (r) => buyCount(r, 100) >= 2 },
    { name: "mc_le_5000_buy1000_ge_3", test: (r) => feature(r, "entry_mc_usd") <= 5000 && buyCount(r, 1000) >= 3 },
  ];
}

function summarize(rows) {
  const pnls = rows.map((r) => num(r.pnlPct)).filter(Number.isFinite);
  const wins = rows.filter((r) => r.outcome === "TP" || num(r.pnlPct) > 0);
  const losses = rows.filter((r) => r.outcome === "SL" || num(r.pnlPct) < 0);
  return {
    trades: rows.length,
    uniqueMints: new Set(rows.map((r) => r.mint)).size,
    uniqueOpportunities: new Set(rows.map((r) => r.opportunityKey)).size,
    wins: wins.length,
    losses: losses.length,
    winRate: pct(wins.length, rows.length),
    grossProfit: sum(pnls.filter((x) => x > 0)),
    grossLoss: sum(pnls.filter((x) => x < 0)),
    net: sum(pnls),
    expectancy: pnls.length ? sum(pnls) / pnls.length : null,
    median: median(pnls),
    stddev: stddev(pnls),
    pf: pf(pnls),
    drawdown: maxDrawdown(rows),
    losingStreak: losingStreak(rows),
    avgHoldMs: mean(rows.map((r) => r.exitMs)),
    medianHoldMs: median(rows.map((r) => r.exitMs)),
    firstAtMs: rows[0]?.entryAtMs ?? null,
    lastAtMs: rows.at(-1)?.entryAtMs ?? null,
  };
}

function maxDrawdown(rows) {
  let equity = 0;
  let high = 0;
  let dd = 0;
  for (const row of rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    equity += num(row.pnlPct, 0);
    high = Math.max(high, equity);
    dd = Math.min(dd, equity - high);
  }
  return dd;
}

function losingStreak(rows) {
  let best = 0;
  let current = 0;
  for (const row of rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    if (row.outcome === "SL" || num(row.pnlPct, 0) < 0) current += 1;
    else current = 0;
    best = Math.max(best, current);
  }
  return best;
}

function split70(rows) {
  const sorted = rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs);
  const cut = Math.floor(sorted.length * 0.7);
  return { train: sorted.slice(0, cut), validation: sorted.slice(cut) };
}

function applyRule(rows, rule) {
  return rows.filter(rule.test);
}

function sourceBreakdown(rows) {
  const groups = new Map();
  for (const row of rows) {
    const key = row.labelSource || "unknown";
    const arr = groups.get(key) || [];
    arr.push(row);
    groups.set(key, arr);
  }
  return [...groups.entries()].map(([source, group]) => ({ source, ...summarize(group) })).sort((a, b) => b.trades - a.trades);
}

function thresholdHeatmap(train, validation) {
  const out = [];
  for (const w of windows) {
    const coverage = pct(validation.filter((r) => Number.isFinite(buyCount(r, w))).length, validation.length);
    for (const c of counts) {
      const rule = { test: (r) => buyCount(r, w) >= c };
      const rows = applyRule(validation, rule);
      if (!rows.length) {
        out.push({ window: w, count: c, coverage, trades: 0 });
        continue;
      }
      out.push({ window: w, count: c, coverage, ...summarize(rows) });
    }
  }
  return out;
}

function mcInteractions(validation) {
  const burstRules = [
    { name: "buy100_ge_2", test: (r) => buyCount(r, 100) >= 2 },
    { name: "buy250_ge_3", test: (r) => buyCount(r, 250) >= 3 },
    { name: "buy500_ge_3", test: (r) => buyCount(r, 500) >= 3 },
    { name: "buy1000_ge_3", test: (r) => buyCount(r, 1000) >= 3 },
  ];
  const out = [];
  for (const br of burstRules) {
    const base = validation.filter(br.test);
    if (base.length < minRows) continue;
    out.push({ rule: br.name, mcCap: "none", sampleLoss: 0, ...summarize(base) });
    for (const cap of mcCaps) {
      const rows = base.filter((r) => feature(r, "entry_mc_usd") <= cap);
      if (rows.length < Math.max(10, Math.floor(minRows / 2))) continue;
      out.push({ rule: br.name, mcCap: cap, sampleLoss: base.length - rows.length, ...summarize(rows) });
    }
  }
  return out.sort((a, b) => b.pf - a.pf || b.expectancy - a.expectancy);
}

function filterInteractions(validation, baseRule) {
  const base = validation.filter(baseRule.test);
  const filters = [
    { name: "dom_le_050", test: (r) => feature(r, "dominant_buy_share_60") <= 0.50 },
    { name: "dom_le_055", test: (r) => feature(r, "dominant_buy_share_60") <= 0.55 },
    { name: "fb_le_2500bps", test: (r) => feature(r, "first_buyer_quote_share_bps") <= 2500 },
    { name: "fb_le_5000bps", test: (r) => feature(r, "first_buyer_quote_share_bps") <= 5000 },
    { name: "unique_ge_3", test: (r) => feature(r, "unique_wallets_60") >= 3 },
    { name: "unique_ge_4", test: (r) => feature(r, "unique_wallets_60") >= 4 },
    { name: "sell_pressure_eq_0", test: (r) => feature(r, "sell_pressure_60") === 0 },
    { name: "net_buy_ge_2", test: (r) => feature(r, "net_buy_sol_60") >= 2 },
    { name: "net_buy_1_4", test: (r) => {
      const v = feature(r, "net_buy_sol_60");
      return v >= 1 && v <= 4;
    } },
  ];
  const baseStats = summarize(base);
  return filters.map((f) => {
    const rows = base.filter(f.test);
    const removed = base.filter((r) => !f.test(r));
    return {
      filter: f.name,
      baseTrades: base.length,
      tradesRemoved: removed.length,
      winnersRemoved: removed.filter((r) => r.outcome === "TP" || num(r.pnlPct) > 0).length,
      losersRemoved: removed.filter((r) => r.outcome === "SL" || num(r.pnlPct) < 0).length,
      deltaExpectancy: summarize(rows).expectancy - baseStats.expectancy,
      ...summarize(rows),
    };
  }).filter((r) => r.trades >= 10).sort((a, b) => b.deltaExpectancy - a.deltaExpectancy);
}

function avoidRules(validation, baseRule) {
  const base = validation.filter(baseRule.test);
  const avoids = [
    { name: "avoid_mc_gt_6000", avoid: (r) => feature(r, "entry_mc_usd") > 6000 },
    { name: "avoid_mc_gt_5000", avoid: (r) => feature(r, "entry_mc_usd") > 5000 },
    { name: "avoid_dom_gt_060", avoid: (r) => feature(r, "dominant_buy_share_60") > 0.60 },
    { name: "avoid_dom_gt_055", avoid: (r) => feature(r, "dominant_buy_share_60") > 0.55 },
    { name: "avoid_fb_gt_6000bps", avoid: (r) => feature(r, "first_buyer_quote_share_bps") > 6000 },
    { name: "avoid_fb_gt_5000bps", avoid: (r) => feature(r, "first_buyer_quote_share_bps") > 5000 },
    { name: "avoid_unique_lt_3", avoid: (r) => feature(r, "unique_wallets_60") < 3 },
    { name: "avoid_net_buy_gt_6", avoid: (r) => feature(r, "net_buy_sol_60") > 6 },
    { name: "avoid_creation_age_gt_1500", avoid: (r) => feature(r, "creation_age_ms") > 1500 },
  ];
  const baseStats = summarize(base);
  return avoids.map((rule) => {
    const removed = base.filter(rule.avoid);
    const kept = base.filter((r) => !rule.avoid(r));
    const s = summarize(kept);
    return {
      avoid: rule.name,
      baseTrades: base.length,
      removed: removed.length,
      winnersRemoved: removed.filter((r) => r.outcome === "TP" || num(r.pnlPct) > 0).length,
      losersRemoved: removed.filter((r) => r.outcome === "SL" || num(r.pnlPct) < 0).length,
      deltaExpectancy: s.expectancy - baseStats.expectancy,
      ...s,
    };
  }).filter((r) => r.trades >= 10).sort((a, b) => b.deltaExpectancy - a.deltaExpectancy);
}

function capacityReplay(rows, maxOpen) {
  const open = [];
  const taken = [];
  const blocked = [];
  for (const row of rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    while (open.length && open[0].exitAtMs <= row.entryAtMs) open.shift();
    if (open.length >= maxOpen) {
      blocked.push(row);
      continue;
    }
    const exitAtMs = row.entryAtMs + Math.min(num(row.exitMs, primaryHoldMs), primaryHoldMs);
    taken.push(row);
    open.push({ exitAtMs });
    open.sort((a, b) => a.exitAtMs - b.exitAtMs);
  }
  return {
    maxOpen,
    blocked: blocked.length,
    winnersBlocked: blocked.filter((r) => r.outcome === "TP" || num(r.pnlPct) > 0).length,
    losersBlocked: blocked.filter((r) => r.outcome === "SL" || num(r.pnlPct) < 0).length,
    peakExposure: maxOpen,
    utilization: utilization(taken, maxOpen),
    ...summarize(taken),
  };
}

function utilization(rows, maxOpen) {
  if (!rows.length) return 0;
  const start = rows[0].entryAtMs;
  const end = Math.max(...rows.map((r) => r.entryAtMs + Math.min(num(r.exitMs, primaryHoldMs), primaryHoldMs)));
  const exposureMs = sum(rows.map((r) => Math.min(num(r.exitMs, primaryHoldMs), primaryHoldMs)));
  return end > start ? pct(exposureMs, (end - start) * maxOpen) : 0;
}

function rescoreRows(rows, tp, sl, holdMs) {
  return rows.map((r) => {
    const score = r._livePnlMarks?.length ? scorePnlMarks(r.entryAtMs, r._livePnlMarks, tp, sl, holdMs) : scoreMcMarks(feature(r, "entry_mc_usd"), r.entryAtMs, r._capsuleMarks, tp, sl, holdMs);
    return { ...r, outcome: score.outcome, pnlPct: score.pnlPct ?? 0, exitMs: score.exitMs ?? holdMs };
  }).filter((r) => Number.isFinite(r.pnlPct));
}

function tpSlHoldOos(train, validation, candidateRule) {
  const trainBase = train.filter(candidateRule.test);
  const valBase = validation.filter(candidateRule.test);
  const trainResults = [];
  for (const tp of tpList) {
    for (const sl of slList) {
      for (const hold of holdList) {
        const scored = rescoreRows(trainBase, tp, sl, hold);
        if (scored.length < minRows) continue;
        trainResults.push({ tp, sl, hold, ...summarize(scored) });
      }
    }
  }
  const candidates = trainResults.sort((a, b) => b.pf - a.pf || b.net - a.net).slice(0, 8);
  return candidates.map((c) => {
    const scored = rescoreRows(valBase, c.tp, c.sl, c.hold);
    return { tp: c.tp, sl: c.sl, hold: c.hold, trainPF: c.pf, trainTrades: c.trades, ...summarize(scored) };
  }).sort((a, b) => b.pf - a.pf || b.net - a.net);
}

function walkForward(rows) {
  const sorted = rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs);
  const foldSize = Math.floor(sorted.length / 5);
  const candidates = [
    ...ruleDefs(),
    { name: "fast250_mc5000", test: (r) => buyCount(r, 250) >= 3 && feature(r, "entry_mc_usd") <= 5000 },
    { name: "fast100_mc5000", test: (r) => buyCount(r, 100) >= 2 && feature(r, "entry_mc_usd") <= 5000 },
    { name: "fast250_mc5000_dom55", test: (r) => buyCount(r, 250) >= 3 && feature(r, "entry_mc_usd") <= 5000 && feature(r, "dominant_buy_share_60") <= 0.55 },
  ];
  const folds = [];
  for (let i = 1; i < 5; i++) {
    const train = sorted.slice(0, foldSize * i);
    const test = sorted.slice(foldSize * i, foldSize * (i + 1));
    if (train.length < minRows || test.length < minRows) continue;
    const trainRank = candidates.map((rule) => ({ rule, rows: train.filter(rule.test) }))
      .filter((x) => x.rows.length >= minRows)
      .map((x) => ({ rule: x.rule, ...summarize(x.rows) }))
      .sort((a, b) => b.pf - a.pf || b.expectancy - a.expectancy);
    const chosen = trainRank[0]?.rule;
    const tested = chosen ? test.filter(chosen.test) : [];
    folds.push({
      fold: i,
      trainRows: train.length,
      testRows: test.length,
      chosen: chosen?.name || "none",
      testStart: test[0]?.entryAtMs,
      testEnd: test.at(-1)?.entryAtMs,
      ...summarize(tested),
    });
  }
  return folds;
}

function timeStability(rows, rule, blocks = 6) {
  const subset = rows.filter(rule.test).sort((a, b) => a.entryAtMs - b.entryAtMs);
  const out = [];
  const blockSize = Math.ceil(subset.length / blocks);
  for (let i = 0; i < blocks; i++) {
    const part = subset.slice(i * blockSize, (i + 1) * blockSize);
    if (!part.length) continue;
    out.push({ block: i + 1, start: part[0].entryAtMs, end: part.at(-1).entryAtMs, ...summarize(part) });
  }
  return out;
}

function bootstrap(rows, iterations = 500) {
  const pnls = rows.map((r) => num(r.pnlPct)).filter(Number.isFinite);
  if (pnls.length < 5) return null;
  const exp = [];
  const wr = [];
  const pfs = [];
  let seed = 1337;
  function rand() {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 2 ** 32;
  }
  for (let i = 0; i < iterations; i++) {
    const sample = [];
    for (let j = 0; j < pnls.length; j++) sample.push(pnls[Math.floor(rand() * pnls.length)]);
    exp.push(mean(sample));
    wr.push(pct(sample.filter((x) => x > 0).length, sample.length));
    pfs.push(pf(sample));
  }
  return {
    iterations,
    wrP05: quantile(wr, 0.05),
    wrP50: quantile(wr, 0.5),
    wrP95: quantile(wr, 0.95),
    expP05: quantile(exp, 0.05),
    expP50: quantile(exp, 0.5),
    expP95: quantile(exp, 0.95),
    pfP05: quantile(pfs.filter(Number.isFinite), 0.05),
    pfP50: quantile(pfs.filter(Number.isFinite), 0.5),
    pfP95: quantile(pfs.filter(Number.isFinite), 0.95),
  };
}

function sizeSensitivity(rows, baseSizeSol = 1) {
  const sizes = [0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1];
  return sizes.map((size) => {
    const scalePenaltyPct = Math.max(0, size - baseSizeSol) * 0;
    const adjusted = rows.map((r) => ({ ...r, pnlPct: num(r.pnlPct, 0) - scalePenaltyPct }));
    return { name: `${size} SOL`, size, assumption: "linear proxy; nonlinear curve replay pending", ...summarize(adjusted) };
  });
}

function nearestNeighbor(train, validation) {
  const specs = ["entry_mc_usd", "creation_age_ms", "net_buy_sol_60", "buy_count_100ms", "buy_count_250ms", "buy_count_500ms", "buy_count_1000ms", "dominant_buy_share_60", "first_buyer_quote_share_bps", "unique_wallets_60"];
  const stat = new Map(specs.map((s) => {
    const vals = train.map((r) => feature(r, s)).filter(Number.isFinite);
    return [s, { median: median(vals), iqr: Math.max(1e-9, quantile(vals, 0.75) - quantile(vals, 0.25)) }];
  }));
  function vec(r) {
    return specs.map((s) => {
      const v = feature(r, s);
      const st = stat.get(s);
      return Number.isFinite(v) ? (v - st.median) / st.iqr : 0;
    });
  }
  const trainVecs = train.map((row) => ({ row, vec: vec(row) }));
  const valVecs = validation.slice(0, 1500).map((row) => ({ row, vec: vec(row) }));
  const out = [];
  for (const k of [10, 25, 50, 100]) {
    const preds = [];
    for (const item of valVecs) {
      const neigh = trainVecs.map((other) => ({ row: other.row, d: dist(item.vec, other.vec) })).sort((a, b) => a.d - b.d).slice(0, k).map((x) => x.row);
      preds.push({
        actualPnl: num(item.row.pnlPct),
        actualWin: item.row.outcome === "TP" || num(item.row.pnlPct) > 0,
        neighborExpectancy: mean(neigh.map((r) => r.pnlPct)),
        neighborWinRate: pct(neigh.filter((r) => r.outcome === "TP" || num(r.pnlPct) > 0).length, neigh.length),
      });
    }
    const selected = preds.filter((p) => p.neighborExpectancy > 0 && p.neighborWinRate >= 50);
    out.push({ name: `nn_k${k}`, k, validationRows: preds.length, selected: selected.length, ...summarize(selected.map((p, idx) => ({ mint: String(idx), opportunityKey: String(idx), entryAtMs: idx, outcome: p.actualWin ? "TP" : "SL", pnlPct: p.actualPnl, exitMs: primaryHoldMs }))) });
  }
  return out;
}

function dist(a, b) {
  return Math.sqrt(sum(a.map((x, i) => (x - b[i]) ** 2)));
}

function printStatsRows(rows, title) {
  console.log(`## ${title}`);
  console.log("");
  console.log(markdownTable(rows, [
    { label: "Strategy", value: (r) => r.name || r.rule || r.filter || r.avoid || r.source || r.label || r.chosen || r.block || r.window },
    { label: "Trades", value: (r) => r.trades },
    { label: "Unique mints", value: (r) => r.uniqueMints },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "Gross profit", value: (r) => fmtPct(r.grossProfit) },
    { label: "Gross loss", value: (r) => fmtPct(r.grossLoss) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Net", value: (r) => fmtPct(r.net) },
    { label: "Exp/trade", value: (r) => fmtPct(r.expectancy) },
    { label: "Median", value: (r) => fmtPct(r.median) },
    { label: "DD", value: (r) => fmtPct(r.drawdown) },
    { label: "Loss streak", value: (r) => r.losingStreak },
  ]));
  console.log("");
}

async function main() {
  const receiptResult = await loadReceiptLanes();
  const inputResult = await loadInputs(receiptResult.inputHashes);
  const allMints = new Set();
  for (const lane of receiptResult.lanes.values()) for (const r of lane.receipts) allMints.add(r.mint);
  const capsuleResult = await loadCapsuleMarks(allMints);
  const liveResult = loadLiveAuditModels();
  const rawRows = buildRows(receiptResult.lanes, inputResult.inputs, capsuleResult.marks, liveResult.models);
  for (const row of rawRows) {
    row._capsuleMarks = capsuleResult.marks.get(row.mint) || [];
    row._livePnlMarks = liveResult.models.get(row.mint)?.exitMarks || [];
  }
  const parentRaw = rawRows.filter((r) => r.lane === "parent").sort((a, b) => a.entryAtMs - b.entryAtMs);
  const parent = dedupe(parentRaw, "mint");
  const split = split70(parent);
  const core = ruleDefs().map((rule) => ({ name: rule.name, rawRows: parentRaw.filter(rule.test).length, ...summarize(parent.filter(rule.test)) }));
  const oosCore = ruleDefs().map((rule) => ({ name: rule.name, trainTrades: split.train.filter(rule.test).length, ...summarize(split.validation.filter(rule.test)) }));
  const heat = thresholdHeatmap(split.train, split.validation);
  const interactions = mcInteractions(split.validation);
  const bestBurstRule = { name: "fast_burst_buy100_ge_2", test: (r) => buyCount(r, 100) >= 2 };
  const filters = filterInteractions(split.validation, bestBurstRule);
  const avoids = avoidRules(split.validation, bestBurstRule);
  const wf = walkForward(parent);
  const stabilityRule = { name: "fast_burst_buy100_ge_2", test: (r) => buyCount(r, 100) >= 2 };
  const stability = timeStability(parent, stabilityRule);
  const bestValidationRule = { name: "fast_burst_buy100_ge_2", test: (r) => buyCount(r, 100) >= 2 };
  const bestRows = split.validation.filter(bestValidationRule.test);
  const boot = bootstrap(bestRows);
  const capacity = maxOpenValues.map((n) => ({ name: `max_open_${n}`, ...capacityReplay(bestRows, n) }));
  const tpSlHold = tpSlHoldOos(split.train, split.validation, bestValidationRule);
  const nn = nearestNeighbor(split.train, split.validation);
  const labelSources = sourceBreakdown(parent);
  const overlaps = ruleDefs().map((a) => ruleDefs().map((b) => {
    const aa = new Set(parent.filter(a.test).map((r) => r.mint));
    const bb = new Set(parent.filter(b.test).map((r) => r.mint));
    const inter = [...aa].filter((m) => bb.has(m)).length;
    return { a: a.name, b: b.name, overlap: inter, aSize: aa.size, bSize: bb.size };
  })).flat();

  console.log("# Promotion-Quality Executable Benchmark Audit");
  console.log("");
  console.log("Read-only analytics only. No live strategy/config/restart/deployment changes.");
  console.log("");
  console.log("## 1. Effective Sample Size / Duplication Audit");
  console.log("");
  const inventory = [...receiptResult.lanes.values()].map((lane) => {
    const rows = rawRows.filter((r) => r.lane === lane.spec.name);
    return {
      lane: lane.spec.name,
      rawRows: rows.length,
      uniqueMints: new Set(rows.map((r) => r.mint)).size,
      uniqueOpportunities: new Set(rows.map((r) => r.opportunityKey)).size,
      duplicateHashes: lane.duplicateHashes,
      statuses: [...lane.statusCounts.entries()].map(([k, v]) => `${k}:${v}`).join(", "),
    };
  });
  console.log(markdownTable(inventory, [
    { label: "Lane", value: (r) => r.lane },
    { label: "Raw rows", value: (r) => r.rawRows },
    { label: "Unique mints", value: (r) => r.uniqueMints },
    { label: "Unique entries", value: (r) => r.uniqueOpportunities },
    { label: "Duplicate receipts", value: (r) => r.duplicateHashes },
    { label: "Statuses", value: (r) => r.statuses },
  ]));
  console.log("");
  console.log(`Decision inputs scanned: ${inputResult.total}; hydrated inputs: ${inputResult.inputs.size}`);
  console.log(`Capsules scanned: ${capsuleResult.total}; marks used: ${capsuleResult.used}; mints with marks: ${capsuleResult.marks.size}`);
  console.log(`Live selected rows: ${liveResult.selectedCount}; live audit rows: ${liveResult.auditCount}; live mints with audit model: ${liveResult.models.size}`);
  console.log("");

  console.log("## 2. Fingerprint Overlap Matrix");
  console.log("");
  console.log(markdownTable(overlaps.filter((r) => r.a !== r.b), [
    { label: "A", value: (r) => r.a },
    { label: "B", value: (r) => r.b },
    { label: "Overlap mints", value: (r) => r.overlap },
    { label: "A size", value: (r) => r.aSize },
    { label: "B size", value: (r) => r.bSize },
    { label: "A overlap", value: (r) => fmtPct(pct(r.overlap, r.aSize)) },
  ]).split("\n").slice(0, 22).join("\n"));
  console.log("");

  printStatsRows(labelSources, "3. Label-Source Audit");

  console.log("## 4. Exact Economic Assumptions");
  console.log("");
  console.log(markdownTable([
    ["Entry size", "Receipt/input `position_size_lamports`; headline `Net @ 1 SOL` scales proxy PnL as if each trade is 1 SOL."],
    ["TP / SL / hold", `${primaryTp}% / ${primarySl}% / ${primaryHoldMs / 1000}s unless the TP/SL grid section says otherwise.`],
    ["Fees", `${roundTripCostBps} bps round-trip proxy for capsule labels; live watcher labels use recorded sell-quote/cost-basis PnL where available.`],
    ["Entry fill", "Historical non-live rows use decision-time executable receipt and capsule marks; live-fixture rows use watcher economics."],
    ["TP capture", "Model labels pay the configured TP when threshold is first crossed; observed live uses actual recorded exit PnL."],
    ["Max-hold", "If no TP/SL within hold, terminal mark PnL is used."],
    ["Failed execution", "Rejected/unavailable receipts are not counted as executed trades in these headline rows."],
    ["Nonlinear size impact", "Not replayed yet from curve reserves; size section is explicitly marked linear proxy."],
  ].map(([item, value]) => ({ item, value })), [
    { label: "Assumption", value: (r) => r.item },
    { label: "Value", value: (r) => r.value },
  ]));
  console.log("");

  printStatsRows(core, "5. Core Candidates On Unique Executable Trades");
  printStatsRows(oosCore, "6. Chronological OOS Core Candidates");

  console.log("## 7. Fast-Burst Threshold Neighborhood OOS");
  console.log("");
  console.log(markdownTable(heat.filter((r) => r.trades >= 10).sort((a, b) => b.pf - a.pf || b.expectancy - a.expectancy).slice(0, 40), [
    { label: "Window", value: (r) => `${r.window}ms` },
    { label: "Buy count", value: (r) => `>=${r.count}` },
    { label: "Coverage", value: (r) => fmtPct(r.coverage) },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Exp", value: (r) => fmtPct(r.expectancy) },
    { label: "DD", value: (r) => fmtPct(r.drawdown) },
  ]));
  console.log("");

  console.log("## 8. Unsupported Burst Windows");
  console.log("");
  console.log(markdownTable(windows.map((w) => ({ window: w, coverage: pct(split.validation.filter((r) => Number.isFinite(buyCount(r, w))).length, split.validation.length) })).filter((r) => r.coverage < 1), [
    { label: "Window", value: (r) => `${r.window}ms` },
    { label: "Feature coverage", value: (r) => fmtPct(r.coverage) },
  ]));
  console.log("");

  console.log("## 9. MC Interaction OOS");
  console.log("");
  console.log(markdownTable(interactions.slice(0, 35), [
    { label: "Burst", value: (r) => r.rule },
    { label: "MC cap", value: (r) => r.mcCap },
    { label: "Sample loss", value: (r) => r.sampleLoss },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Exp", value: (r) => fmtPct(r.expectancy) },
    { label: "DD", value: (r) => fmtPct(r.drawdown) },
  ]));
  console.log("");

  console.log("## 10. Dominance / Concentration / Flow Filter Incremental Value");
  console.log("");
  console.log(markdownTable(filters.slice(0, 20), [
    { label: "Filter", value: (r) => r.filter },
    { label: "Trades", value: (r) => r.trades },
    { label: "Removed", value: (r) => r.tradesRemoved },
    { label: "W removed", value: (r) => r.winnersRemoved },
    { label: "L removed", value: (r) => r.losersRemoved },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Delta exp", value: (r) => fmtPct(r.deltaExpectancy) },
  ]));
  console.log("");

  console.log("## 11. Avoid-Fingerprint Analysis");
  console.log("");
  console.log(markdownTable(avoids.slice(0, 20), [
    { label: "Avoid", value: (r) => r.avoid },
    { label: "Kept trades", value: (r) => r.trades },
    { label: "Removed", value: (r) => r.removed },
    { label: "W removed", value: (r) => r.winnersRemoved },
    { label: "L removed", value: (r) => r.losersRemoved },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Delta exp", value: (r) => fmtPct(r.deltaExpectancy) },
  ]));
  console.log("");

  console.log("## 12. Temporal Shape Proxy");
  console.log("");
  console.log("Current decision inputs expose aggregate buy-count windows and concentration fields, not raw per-buy spacing/size sequences. So this section tests available temporal proxies only; true spacing/size trajectory requires the raw event tape.");
  printStatsRows([
    { name: "buy100_ge_2", ...summarize(split.validation.filter((r) => buyCount(r, 100) >= 2)) },
    { name: "buy250_ge_3", ...summarize(split.validation.filter((r) => buyCount(r, 250) >= 3)) },
    { name: "buy100_ge_2_unique_ge_3", ...summarize(split.validation.filter((r) => buyCount(r, 100) >= 2 && feature(r, "unique_wallets_60") >= 3)) },
    { name: "buy100_ge_2_dom_le_055", ...summarize(split.validation.filter((r) => buyCount(r, 100) >= 2 && feature(r, "dominant_buy_share_60") <= 0.55)) },
    { name: "buy100_ge_2_velocity_mid", ...summarize(split.validation.filter((r) => buyCount(r, 100) >= 2 && feature(r, "velocity_100_to_1000") >= 0.33 && feature(r, "velocity_100_to_1000") <= 0.67)) },
  ], "12B. Temporal Proxy OOS Results");

  printStatsRows(nn, "13. Nearest-Neighbor / Similarity Validation");

  console.log("## 14. Cluster Validation");
  console.log("");
  console.log("Cluster definitions are not promoted in this pass. The simple train-only NN signal is reported instead; full frozen-cluster validation needs raw event-tape features to avoid clustering on weak aggregate fields.");
  console.log("");

  console.log("## 15. True Walk-Forward");
  console.log("");
  console.log(markdownTable(wf, [
    { label: "Fold", value: (r) => r.fold },
    { label: "Train rows", value: (r) => r.trainRows },
    { label: "Test dates", value: (r) => `${new Date(r.testStart).toISOString()}..${new Date(r.testEnd).toISOString()}` },
    { label: "Chosen rule", value: (r) => r.chosen },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Net", value: (r) => fmtPct(r.net) },
    { label: "DD", value: (r) => fmtPct(r.drawdown) },
  ]));
  console.log("");

  console.log("## 16. Time Stability");
  console.log("");
  console.log(markdownTable(stability, [
    { label: "Block", value: (r) => r.block },
    { label: "Dates", value: (r) => `${new Date(r.start).toISOString()}..${new Date(r.end).toISOString()}` },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Exp", value: (r) => fmtPct(r.expectancy) },
  ]));
  console.log("");

  console.log("## 17. Bootstrap / Confidence");
  console.log("");
  console.log(boot ? markdownTable([boot], [
    { label: "Iterations", value: (r) => r.iterations },
    { label: "WR 5/50/95", value: (r) => `${fmtPct(r.wrP05)} / ${fmtPct(r.wrP50)} / ${fmtPct(r.wrP95)}` },
    { label: "Exp 5/50/95", value: (r) => `${fmtPct(r.expP05)} / ${fmtPct(r.expP50)} / ${fmtPct(r.expP95)}` },
    { label: "PF 5/50/95", value: (r) => `${fmt(r.pfP05, 2)} / ${fmt(r.pfP50, 2)} / ${fmt(r.pfP95, 2)}` },
  ]) : "No bootstrap: insufficient rows.");
  console.log("");

  printStatsRows(sizeSensitivity(bestRows), "18. Position-Size Sensitivity");
  printStatsRows(capacity, "19. MAX_OPEN For Current Best Validation Rule");

  console.log("## 20. TP / SL / Hold OOS Research");
  console.log("");
  console.log(markdownTable(tpSlHold.slice(0, 20), [
    { label: "TP", value: (r) => fmtPct(r.tp) },
    { label: "SL", value: (r) => fmtPct(r.sl) },
    { label: "Hold", value: (r) => `${r.hold / 1000}s` },
    { label: "Train PF", value: (r) => Number.isFinite(r.trainPF) ? fmt(r.trainPF, 2) : "inf" },
    { label: "OOS trades", value: (r) => r.trades },
    { label: "OOS WR", value: (r) => fmtPct(r.winRate) },
    { label: "OOS PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "OOS Exp", value: (r) => fmtPct(r.expectancy) },
  ]));
  console.log("");

  console.log("## 21. Cache Availability Interaction");
  console.log("");
  console.log("Not recomputed inside this benchmark script. The latest cache-miss audit remains separate because rejected rows lack high-fidelity live watcher labels. Use it as infra coverage research, not benchmark PnL proof.");
  console.log("");

  const simpleFinalRows = [
    ...oosCore,
    ...interactions.slice(0, 5).map((r) => ({ name: `${r.rule}_mc_${r.mcCap}`, ...r })),
    ...filters.slice(0, 3).map((r) => ({ name: `fast100_${r.filter}`, ...r })),
  ].filter((r) => r.trades >= 10).sort((a, b) => b.pf - a.pf || b.expectancy - a.expectancy);
  const nnRows = nn.map((r) => ({ name: r.name || `nn_k${r.k}`, ...r })).filter((r) => r.trades >= 10);
  const finalRows = [...simpleFinalRows, ...nnRows].sort((a, b) => b.pf - a.pf || b.expectancy - a.expectancy);
  printStatsRows(finalRows.slice(0, 20), "22. Fair Strategy Comparison");

  console.log("## 23. Promotion Criteria");
  console.log("");
  console.log("- Minimum: corrected label validation must stay coherent as live validation grows beyond the current tiny live set.");
  console.log("- Minimum unique OOS trades should exceed the larger of 200 trades or a full-day sample for this lane, because current validation variance is still material.");
  console.log("- OOS PF should remain comfortably above 1.2 after capacity and realistic fees; PF near 1.1 is too fragile for 0.5-1 SOL sizing.");
  console.log("- At least 3 of 4 walk-forward test folds should be positive, with no single fold carrying most of the total PnL.");
  console.log("- Threshold neighborhood must show a plateau across adjacent windows/counts; one exact threshold is research-only.");
  console.log("- Size promotion requires nonlinear curve replay, not the linear proxy in this script.");
  console.log("");

  console.log("## 24. New Benchmark");
  console.log("");
  const simpleCandidate = simpleFinalRows.find((row) => row.trades >= 200 && row.pf > 1.2 && row.expectancy > 0);
  const bestNn = nnRows.sort((a, b) => b.pf - a.pf || b.expectancy - a.expectancy)[0];
  const candidate = simpleCandidate;
  if (candidate) {
    console.log("Candidate for paper/live-parity benchmark, not deployment:");
    console.log(`EXECUTABLE_STRATEGY_BENCHMARK_V1 = ${candidate.name}`);
    console.log(`Expected OOS: trades=${candidate.trades}, WR=${fmtPct(candidate.winRate)}, PF=${Number.isFinite(candidate.pf) ? fmt(candidate.pf, 2) : "inf"}, expectancy=${fmtPct(candidate.expectancy)}.`);
    if (bestNn) {
      const nnEdgeLift = candidate.expectancy ? ((bestNn.expectancy / candidate.expectancy) - 1) * 100 : null;
      console.log(`Best NN research result: ${bestNn.name}, PF=${fmt(bestNn.pf, 2)}, expectancy=${fmtPct(bestNn.expectancy)}, lift vs simple=${fmtPct(nnEdgeLift)}. Treat NN as research unless its lift persists after raw-event trajectory features and implementation-cost audit.`);
    }
    console.log("Do not deploy until nonlinear size replay, cache interaction, and additional live-label validation pass.");
  } else {
    console.log("No promotion-quality benchmark is signed off yet.");
  }
  console.log("");

  console.log("## Final Direct Answers");
  console.log("");
  console.log(`A. Apparent executable edge after dedupe: ${oosCore[0] ? "yes, but benchmark-level confidence is still pending live-label growth and nonlinear sizing." : "not proven."}`);
  console.log("B. Fast-burst predictiveness: see threshold neighborhood. Only supported windows can be trusted; unsupported windows require raw event tape.");
  console.log("C. MC interaction: see MC table; promote only if sample loss is acceptable versus expectancy gain.");
  console.log("D. Avoid fingerprint: see avoid table; the best avoid is the one with positive delta expectancy and loser-heavy removals.");
  console.log("E. Similarity: compare NN table against simple rules; prefer simple rule unless NN materially improves OOS.");
  console.log("F. Temporal trajectories: aggregate temporal proxies are useful, but raw spacing/size event tape is needed for real trajectory modeling.");
  console.log("G. Walk-forward: see fold table; this is the real anti-overfit check.");
  console.log("H. Size sensitivity: linear proxy only. Nonlinear Pump curve replay remains mandatory before 0.5-1 SOL.");
  console.log("I. MAX_OPEN: see capacity table for the best validation rule.");
  console.log("J. TP/SL/hold: see OOS grid; prefer broad plateaus over best train PF.");
  console.log(`K. Strongest current simple promotion candidate: ${candidate?.name || "none"}.`);
  console.log("L. Confidence: promising, not production-signed; the live-label validation set is still small and size impact is not yet replayed from reserves.");
}

main().catch((err) => {
  console.error(err?.stack || err);
  process.exit(1);
});
