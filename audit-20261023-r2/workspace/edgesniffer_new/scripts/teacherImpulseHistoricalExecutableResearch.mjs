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

const sinceMs = process.env.REPORT_SINCE_MS ? Number(process.env.REPORT_SINCE_MS) : Date.parse(process.env.REPORT_SINCE || "1970-01-01T00:00:00Z");
const selectedSinceMs = process.env.LIVE_REPORT_SINCE_MS ? Number(process.env.LIVE_REPORT_SINCE_MS) : Date.parse(process.env.LIVE_REPORT_SINCE || "2026-08-29T23:06:27Z");
const tpPcts = parseList(process.env.TP_PCTS || "10,15,20,25,30");
const slPcts = parseList(process.env.SL_PCTS || "-10,-15,-20,-25");
const holdMsList = parseList(process.env.HOLD_MS || "15000,30000,45000,60000,90000,120000");
const primaryTp = Number(process.env.PRIMARY_TP_PCT || 20);
const primarySl = Number(process.env.PRIMARY_SL_PCT || -20);
const primaryHoldMs = Number(process.env.PRIMARY_HOLD_MS || 120000);
const roundTripCostBps = Number(process.env.ROUND_TRIP_COST_BPS || 250);
const minPocketSample = Number(process.env.MIN_POCKET_SAMPLE || 25);
const maxCapsuleMc = Number(process.env.MAX_CAPSULE_MC_USD || 500000);
const maxFeatureSamples = Number(process.env.MAX_FEATURE_SAMPLES || 20000);
let liveSanityStats = { selected: 0, audits: 0, exitDecisions: 0, positiveExitDecisions: 0 };
const sanityFixtureMints = new Set([
  "pntWJFjV4TubWAkU93jMTSWFbZZLoRmvqcsim45pump",
  "7kTucFx793ugrKRcYaHw4f6k5CszjMKLqiAVXwcFpump",
]);

function parseList(raw) {
  return String(raw).split(",").map((part) => Number(part.trim())).filter(Number.isFinite);
}

function num(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
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
    ?? num(row?.canonical_state?.observed_at_ms)
    ?? num(row?.observedAtMs)
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

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const mid = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[mid] : (xs[mid - 1] + xs[mid]) / 2;
}

function quantile(values, q) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const idx = Math.min(xs.length - 1, Math.max(0, Math.floor((xs.length - 1) * q)));
  return xs[idx];
}

function sum(values) {
  return values.filter(Number.isFinite).reduce((acc, value) => acc + value, 0);
}

function pf(pnls) {
  const gp = sum(pnls.filter((v) => v > 0));
  const gl = Math.abs(sum(pnls.filter((v) => v < 0)));
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
      // Ignore corrupt/truncated lines in analytics mode.
    }
  }
}

function parseLaneSpec(spec) {
  const idx = spec.indexOf("=");
  if (idx < 0) throw new Error(`bad lane spec: ${spec}`);
  return { name: spec.slice(0, idx), file: spec.slice(idx + 1) };
}

function recordFeaturePaths(value, prefix, dict, rowStats) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return;
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key;
    if (child && typeof child === "object" && !Array.isArray(child)) {
      recordFeaturePaths(child, path, dict, rowStats);
      continue;
    }
    const stat = dict.get(path) || { path, count: 0, typeCounts: new Map(), examples: [] };
    stat.count += 1;
    const type = Array.isArray(child) ? "array" : child === null ? "null" : typeof child;
    stat.typeCounts.set(type, (stat.typeCounts.get(type) || 0) + 1);
    if (stat.examples.length < 3 && child != null) stat.examples.push(String(child).slice(0, 80));
    dict.set(path, stat);
    rowStats.totalRowsWithAnyField += 0;
  }
}

const featureSpecs = [
  { name: "entry_mc_usd", source: "receipt.calculated_market_cap_usd/input.candidate.market_cap_usd", path: "entry_mc_usd", units: "USD", leak: "none" },
  { name: "creation_age_ms", source: "input.candidate.creation_age_ms", path: "features.creation_age_ms", units: "ms", leak: "none" },
  { name: "net_buy_sol_60", source: "input.candidate.net_buy_sol_60", path: "features.net_buy_sol_60", units: "SOL", leak: "none" },
  { name: "buy_count_100ms", source: "input.candidate.buy_count_100ms", path: "features.buy_count_100ms", units: "count", leak: "none" },
  { name: "buy_count_250ms", source: "input.candidate.buy_count_250ms", path: "features.buy_count_250ms", units: "count", leak: "none" },
  { name: "buy_count_500ms", source: "input.candidate.buy_count_500ms", path: "features.buy_count_500ms", units: "count", leak: "none" },
  { name: "buy_count_1000ms", source: "input.candidate.buy_count_1000ms", path: "features.buy_count_1000ms", units: "count", leak: "none" },
  { name: "buy_count_60", source: "input.candidate.buy_count_60", path: "features.buy_count_60", units: "count", leak: "none" },
  { name: "sell_pressure_60", source: "input.candidate.sell_pressure_60", path: "features.sell_pressure_60", units: "ratio", leak: "none" },
  { name: "dominant_buy_share_60", source: "input.candidate.dominant_buy_share_60", path: "features.dominant_buy_share_60", units: "ratio", leak: "none" },
  { name: "unique_wallets_60", source: "input.candidate.unique_wallets_60", path: "features.unique_wallets_60", units: "count", leak: "none" },
  { name: "unique_per_buy_60", source: "input.candidate.unique_per_buy_60", path: "features.unique_per_buy_60", units: "ratio", leak: "none" },
  { name: "first_buyer_quote_share_bps", source: "input.candidate.first_buyer_quote_share_bps", path: "features.first_buyer_quote_share_bps", units: "bps", leak: "none" },
  { name: "largest_wallet_quote_share_bps", source: "input.candidate.largest_wallet_quote_share_bps", path: "features.largest_wallet_quote_share_bps", units: "bps", leak: "none" },
  { name: "expected_tokens_out_raw", source: "input.execution.expected_tokens_out_raw", path: "features.expected_tokens_out_raw", units: "raw tokens", leak: "none" },
  { name: "min_tokens_out_raw", source: "input.execution.min_tokens_out_raw", path: "features.min_tokens_out_raw", units: "raw tokens", leak: "none" },
  { name: "position_size_lamports", source: "input.execution.position_size_lamports", path: "features.position_size_lamports", units: "lamports", leak: "none" },
  { name: "priority_fee_lamports", source: "input.execution.priority_fee_lamports", path: "features.priority_fee_lamports", units: "lamports", leak: "infra only" },
  { name: "jito_tip_lamports", source: "input.execution.jito_tip_lamports", path: "features.jito_tip_lamports", units: "lamports", leak: "infra only" },
  { name: "state_age_ms", source: "candidate_ready_ts_ms - canonical_state.observed_at_ms", path: "features.state_age_ms", units: "ms", leak: "infra only" },
  { name: "token_program_2022", source: "input.candidate.token_program", path: "features.token_program_2022", units: "boolean", leak: "none" },
  { name: "has_source_ix_data", source: "input.candidate.source_ix_data_base64", path: "features.has_source_ix_data", units: "boolean", leak: "none" },
  { name: "has_source_ix_accounts", source: "input.extra_decision_fields.sourceIxAccounts", path: "features.has_source_ix_accounts", units: "boolean", leak: "none" },
  { name: "velocity_100_to_1000", source: "buy_count_100ms/buy_count_1000ms", path: "derived.velocity_100_to_1000", units: "ratio", leak: "none" },
  { name: "accel_1000_minus_500", source: "buy_count_1000ms - buy_count_500ms", path: "derived.accel_1000_minus_500", units: "count", leak: "none" },
];

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
    buy_count_100ms: num(c.buy_count_100ms),
    buy_count_250ms: num(c.buy_count_250ms),
    buy_count_500ms: num(c.buy_count_500ms),
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
  f.accel_1000_minus_500 = f.buy_count_1000ms != null && f.buy_count_500ms != null
    ? f.buy_count_1000ms - f.buy_count_500ms
    : null;
  return f;
}

function scoreMarks(entryMc, entryAtMs, marks, tp, sl, holdMs) {
  if (!Number.isFinite(entryMc) || entryMc <= 0 || !marks?.length) {
    return { outcome: "missing", pnlPct: null, exitMs: null, maxRoiPct: null, minRoiPct: null };
  }
  let firstTp = null;
  let firstSl = null;
  let terminal = null;
  let maxRoi = -Infinity;
  let minRoi = Infinity;
  let peakAt = null;
  const costPct = roundTripCostBps / 100;
  for (const mark of marks) {
    const dt = mark.tsMs - entryAtMs;
    if (dt < 0 || dt > holdMs) continue;
    const roi = ((mark.mcUsd / entryMc) - 1) * 100 - costPct;
    if (roi > maxRoi) {
      maxRoi = roi;
      peakAt = dt;
    }
    if (roi < minRoi) minRoi = roi;
    terminal = { roi, dt };
    if (firstTp == null && roi >= tp) firstTp = { roi, dt };
    if (firstSl == null && roi <= sl) firstSl = { roi, dt };
  }
  if (firstTp && (!firstSl || firstTp.dt <= firstSl.dt)) {
    return { outcome: "TP", pnlPct: tp, exitMs: firstTp.dt, maxRoiPct: maxRoi, minRoiPct: minRoi, peakAtMs: peakAt };
  }
  if (firstSl) {
    return { outcome: "SL", pnlPct: sl, exitMs: firstSl.dt, maxRoiPct: maxRoi, minRoiPct: minRoi, peakAtMs: peakAt };
  }
  return {
    outcome: "none",
    pnlPct: terminal ? terminal.roi : null,
    exitMs: terminal ? terminal.dt : holdMs,
    maxRoiPct: Number.isFinite(maxRoi) ? maxRoi : null,
    minRoiPct: Number.isFinite(minRoi) ? minRoi : null,
    peakAtMs: peakAt,
  };
}

function scorePnlMarks(entryAtMs, marks, tp, sl, holdMs) {
  const usable = (marks || [])
    .filter((mark) => Number.isFinite(mark.tsMs) && Number.isFinite(mark.pnlPct))
    .sort((a, b) => a.tsMs - b.tsMs);
  if (!Number.isFinite(entryAtMs) || !usable.length) {
    return { outcome: "missing", pnlPct: null, exitMs: null, maxRoiPct: null, minRoiPct: null };
  }
  let firstTp = null;
  let firstSl = null;
  let terminal = null;
  let maxRoi = -Infinity;
  let minRoi = Infinity;
  let peakAt = null;
  for (const mark of usable) {
    const dt = mark.tsMs - entryAtMs;
    if (dt < 0 || dt > holdMs) continue;
    const roi = mark.pnlPct;
    if (roi > maxRoi) {
      maxRoi = roi;
      peakAt = dt;
    }
    if (roi < minRoi) minRoi = roi;
    terminal = { roi, dt };
    if (firstTp == null && roi >= tp) firstTp = { roi, dt };
    if (firstSl == null && roi <= sl) firstSl = { roi, dt };
  }
  if (firstTp && (!firstSl || firstTp.dt <= firstSl.dt)) {
    return { outcome: "TP", pnlPct: tp, exitMs: firstTp.dt, maxRoiPct: maxRoi, minRoiPct: minRoi, peakAtMs: peakAt };
  }
  if (firstSl) {
    return { outcome: "SL", pnlPct: sl, exitMs: firstSl.dt, maxRoiPct: maxRoi, minRoiPct: minRoi, peakAtMs: peakAt };
  }
  return {
    outcome: "none",
    pnlPct: terminal ? terminal.roi : null,
    exitMs: terminal ? terminal.dt : holdMs,
    maxRoiPct: Number.isFinite(maxRoi) ? maxRoi : null,
    minRoiPct: Number.isFinite(minRoi) ? minRoi : null,
    peakAtMs: peakAt,
  };
}

function liveDecisionPnl(row) {
  return num(row?.feeAdjustedPnlPct) ?? num(row?.pnlPct) ?? num(row?.grossPnlPct);
}

function labelFromLiveDecision(row) {
  if (!row) return { outcome: "missing", pnlPct: null, exitMs: null, reason: null };
  const pnlPct = liveDecisionPnl(row);
  const reason = String(row.reason || row.exitReason || "").toLowerCase();
  let outcome = "none";
  if (reason.includes("take-profit") || reason === "tp") outcome = "TP";
  else if (reason.includes("stop") || reason.includes("hard") || reason === "sl") outcome = "SL";
  else if (Number.isFinite(pnlPct) && pnlPct >= primaryTp) outcome = "TP";
  else if (Number.isFinite(pnlPct) && pnlPct <= primarySl) outcome = "SL";
  return { outcome, pnlPct, exitMs: null, reason: row.reason || row.exitReason || null };
}

function qualityBucket(score) {
  if (!score || !Number.isFinite(score.pnlPct)) return "missing";
  if (score.outcome === "TP" && score.maxRoiPct >= 40) return "A_strong_winner";
  if (score.outcome === "TP") return "B_tp_winner";
  if (score.pnlPct > 3) return "C_positive_no_tp";
  if (score.pnlPct >= -3) return "C_neutral";
  if (score.outcome === "SL" && score.exitMs <= 1000) return "E_fast_sl";
  if (score.outcome === "SL") return "E_hard_sl";
  return "D_loser";
}

function summarize(rows, pnlField = "pnlPct", outcomeField = "outcome") {
  const pnls = rows.map((row) => num(row[pnlField])).filter(Number.isFinite);
  const wins = rows.filter((row) => row[outcomeField] === "TP" || num(row[pnlField]) > 0);
  const losses = rows.filter((row) => row[outcomeField] === "SL" || num(row[pnlField]) < 0);
  return {
    trades: rows.length,
    wins: wins.length,
    losses: losses.length,
    winRate: pct(wins.length, rows.length),
    netPct: sum(pnls),
    netSolAt1Sol: sum(pnls) / 100,
    avgPct: pnls.length ? sum(pnls) / pnls.length : null,
    medianPct: median(pnls),
    p5: quantile(pnls, 0.05),
    p25: quantile(pnls, 0.25),
    p75: quantile(pnls, 0.75),
    p95: quantile(pnls, 0.95),
    pf: pf(pnls),
    worst: pnls.length ? Math.min(...pnls) : null,
    best: pnls.length ? Math.max(...pnls) : null,
  };
}

function summaryRow(name, rows) {
  const s = summarize(rows);
  return {
    name,
    ...s,
  };
}

function featureValue(row, name) {
  return num(row.features?.[name]);
}

function featureBins(rows, feature, maxBins = 8) {
  const values = rows.map((row) => featureValue(row, feature.name)).filter(Number.isFinite);
  if (values.length < Math.max(minPocketSample, 10)) return [];
  const sorted = [...values].sort((a, b) => a - b);
  const cuts = [];
  for (let i = 1; i < maxBins; i++) cuts.push(sorted[Math.floor((sorted.length - 1) * i / maxBins)]);
  const uniqCuts = [...new Set(cuts.map((v) => fmt(v, 6)))].map(Number);
  const bins = [];
  let lo = -Infinity;
  for (const cut of [...uniqCuts, Infinity]) {
    const bucket = rows.filter((row) => {
      const v = featureValue(row, feature.name);
      return Number.isFinite(v) && v > lo && v <= cut;
    });
    if (bucket.length >= minPocketSample) {
      bins.push({
        feature: feature.name,
        range: `${Number.isFinite(lo) ? fmt(lo, 4) : "-inf"}..${Number.isFinite(cut) ? fmt(cut, 4) : "inf"}`,
        ...summarize(bucket),
      });
    }
    lo = cut;
  }
  return bins;
}

function numericStats(rows, feature) {
  const all = rows.map((row) => featureValue(row, feature.name)).filter(Number.isFinite);
  const winners = rows.filter((row) => row.outcome === "TP").map((row) => featureValue(row, feature.name)).filter(Number.isFinite);
  const losers = rows.filter((row) => row.outcome === "SL" || row.pnlPct < 0).map((row) => featureValue(row, feature.name)).filter(Number.isFinite);
  if (!all.length) return null;
  const missing = rows.length - all.length;
  const pooled = Math.sqrt((variance(winners) + variance(losers)) / 2);
  return {
    feature: feature.name,
    count: all.length,
    missing,
    coverage: pct(all.length, rows.length),
    median: median(all),
    p25: quantile(all, 0.25),
    p75: quantile(all, 0.75),
    winnerMedian: median(winners),
    loserMedian: median(losers),
    effect: pooled > 0 && Number.isFinite(median(winners)) && Number.isFinite(median(losers))
      ? (median(winners) - median(losers)) / pooled
      : null,
  };
}

function variance(values) {
  const xs = values.filter(Number.isFinite);
  if (xs.length < 2) return 0;
  const m = sum(xs) / xs.length;
  return sum(xs.map((x) => (x - m) ** 2)) / (xs.length - 1);
}

function pocketRows(rows) {
  const defs = [
    { name: "mc_le_5000", test: (r) => featureValue(r, "entry_mc_usd") <= 5000 },
    { name: "mc_le_5000_dom_le_055", test: (r) => featureValue(r, "entry_mc_usd") <= 5000 && featureValue(r, "dominant_buy_share_60") <= 0.55 },
    { name: "mc_le_5000_fb_le_5000", test: (r) => featureValue(r, "entry_mc_usd") <= 5000 && featureValue(r, "first_buyer_quote_share_bps") <= 5000 },
    { name: "mc_le_5000_buy1000_ge_3", test: (r) => featureValue(r, "entry_mc_usd") <= 5000 && featureValue(r, "buy_count_1000ms") >= 3 },
    { name: "mc_le_5000_buy1000_ge_3_dom_le_055", test: (r) => featureValue(r, "entry_mc_usd") <= 5000 && featureValue(r, "buy_count_1000ms") >= 3 && featureValue(r, "dominant_buy_share_60") <= 0.55 },
    { name: "mc_3500_5000_buy1000_ge_3", test: (r) => between(featureValue(r, "entry_mc_usd"), 3500, 5000) && featureValue(r, "buy_count_1000ms") >= 3 },
    { name: "low_sell_pressure_le_010", test: (r) => featureValue(r, "sell_pressure_60") <= 0.10 },
    { name: "strong_net_buy_ge_1", test: (r) => featureValue(r, "net_buy_sol_60") >= 1 },
    { name: "strong_net_buy_ge_2", test: (r) => featureValue(r, "net_buy_sol_60") >= 2 },
    { name: "fast_burst_buy100_ge_2", test: (r) => featureValue(r, "buy_count_100ms") >= 2 },
    { name: "fast_burst_buy250_ge_3", test: (r) => featureValue(r, "buy_count_250ms") >= 3 },
    { name: "broad_unique_ge_3", test: (r) => featureValue(r, "unique_wallets_60") >= 3 },
  ];
  const out = [];
  for (const def of defs) {
    const subset = rows.filter(def.test);
    if (subset.length >= minPocketSample) out.push(summaryRow(def.name, subset));
  }
  return out.sort((a, b) => b.pf - a.pf || b.netPct - a.netPct);
}

function between(v, lo, hi) {
  return Number.isFinite(v) && v >= lo && v <= hi;
}

function trainValidation(rows) {
  const sorted = rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs);
  const cut = Math.floor(sorted.length * 0.7);
  return { train: sorted.slice(0, cut), validation: sorted.slice(cut) };
}

function maxDrawdown(rows) {
  let equity = 0;
  let high = 0;
  let maxDd = 0;
  for (const row of rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    equity += num(row.pnlPct, 0);
    if (equity > high) high = equity;
    maxDd = Math.min(maxDd, equity - high);
  }
  return maxDd;
}

function losingStreak(rows) {
  let best = 0;
  let current = 0;
  for (const row of rows.slice().sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    if (num(row.pnlPct, 0) < 0 || row.outcome === "SL") current += 1;
    else current = 0;
    best = Math.max(best, current);
  }
  return best;
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
  return { maxOpen, blocked: blocked.length, ...summaryRow(`maxOpen${maxOpen}`, taken) };
}

function buildFeatureVector(row, specs, stats) {
  return specs.map((spec) => {
    const v = featureValue(row, spec.name);
    if (!Number.isFinite(v)) return 0;
    const s = stats.get(spec.name);
    return s?.iqr ? (v - s.median) / s.iqr : 0;
  });
}

function nearestNeighborReport(rows) {
  const usableSpecs = featureSpecs.filter((spec) => {
    const vals = rows.map((row) => featureValue(row, spec.name)).filter(Number.isFinite);
    return vals.length >= rows.length * 0.5 && variance(vals) > 0;
  }).slice(0, 16);
  if (rows.length < 100 || usableSpecs.length < 4) return [];
  const split = trainValidation(rows);
  const stats = new Map();
  for (const spec of usableSpecs) {
    const vals = split.train.map((row) => featureValue(row, spec.name)).filter(Number.isFinite);
    stats.set(spec.name, { median: median(vals), iqr: Math.max(1e-9, quantile(vals, 0.75) - quantile(vals, 0.25)) });
  }
  const trainVecs = split.train.map((row) => ({ row, vec: buildFeatureVector(row, usableSpecs, stats) }));
  const validationVecs = split.validation.slice(0, Math.min(split.validation.length, 1500)).map((row) => ({ row, vec: buildFeatureVector(row, usableSpecs, stats) }));
  const ks = [10, 25, 50, 100].filter((k) => k <= trainVecs.length);
  const out = [];
  for (const k of ks) {
    const predictions = [];
    for (const item of validationVecs) {
      const neighbors = trainVecs
        .map((other) => ({ row: other.row, d: euclidean(item.vec, other.vec) }))
        .sort((a, b) => a.d - b.d)
        .slice(0, k)
        .map((x) => x.row);
      predictions.push({
        neighborWinRate: pct(neighbors.filter((row) => row.outcome === "TP").length, neighbors.length),
        neighborAvgPnl: sum(neighbors.map((row) => row.pnlPct)) / neighbors.length,
        actualPnl: item.row.pnlPct,
        actualWin: item.row.outcome === "TP",
      });
    }
    const top = predictions.filter((row) => row.neighborAvgPnl > 0 && row.neighborWinRate >= 30);
    out.push({
      k,
      validationRows: predictions.length,
      selectedByNeighbor: top.length,
      selectedWinRate: pct(top.filter((row) => row.actualWin).length, top.length),
      selectedAvgPnl: top.length ? sum(top.map((row) => row.actualPnl)) / top.length : null,
      selectedPF: pf(top.map((row) => row.actualPnl)),
    });
  }
  return out;
}

function euclidean(a, b) {
  let acc = 0;
  for (let i = 0; i < Math.min(a.length, b.length); i++) acc += (a[i] - b[i]) ** 2;
  return Math.sqrt(acc);
}

function clusterWinners(rows) {
  const winners = rows.filter((row) => row.outcome === "TP");
  if (winners.length < 20) return [];
  const specs = ["entry_mc_usd", "creation_age_ms", "net_buy_sol_60", "buy_count_1000ms", "sell_pressure_60", "dominant_buy_share_60", "unique_wallets_60", "first_buyer_quote_share_bps"]
    .map((name) => featureSpecs.find((spec) => spec.name === name))
    .filter(Boolean);
  const stats = new Map(specs.map((spec) => {
    const vals = winners.map((row) => featureValue(row, spec.name)).filter(Number.isFinite);
    return [spec.name, { median: median(vals), iqr: Math.max(1e-9, quantile(vals, 0.75) - quantile(vals, 0.25)) }];
  }));
  const points = winners.map((row) => ({ row, vec: buildFeatureVector(row, specs, stats) }));
  const k = Math.min(4, Math.max(2, Math.floor(Math.sqrt(winners.length / 8))));
  let centers = points.slice(0, k).map((p) => p.vec.slice());
  for (let iter = 0; iter < 20; iter++) {
    const groups = Array.from({ length: k }, () => []);
    for (const p of points) {
      let best = 0;
      let bestD = Infinity;
      centers.forEach((c, idx) => {
        const d = euclidean(p.vec, c);
        if (d < bestD) {
          bestD = d;
          best = idx;
        }
      });
      groups[best].push(p);
    }
    centers = groups.map((group, idx) => group.length ? averageVec(group.map((p) => p.vec)) : centers[idx]);
  }
  const groups = Array.from({ length: k }, () => []);
  for (const p of points) {
    let best = 0;
    let bestD = Infinity;
    centers.forEach((c, idx) => {
      const d = euclidean(p.vec, c);
      if (d < bestD) {
        bestD = d;
        best = idx;
      }
    });
    groups[best].push(p.row);
  }
  return groups.map((group, idx) => ({
    cluster: `winner_cluster_${idx + 1}`,
    size: group.length,
    medianMc: median(group.map((row) => featureValue(row, "entry_mc_usd"))),
    medianAge: median(group.map((row) => featureValue(row, "creation_age_ms"))),
    medianBuy1000: median(group.map((row) => featureValue(row, "buy_count_1000ms"))),
    medianNetBuy: median(group.map((row) => featureValue(row, "net_buy_sol_60"))),
    medianDom: median(group.map((row) => featureValue(row, "dominant_buy_share_60"))),
    medianFirstBuyerBps: median(group.map((row) => featureValue(row, "first_buyer_quote_share_bps"))),
    medianTimeToTp: median(group.map((row) => row.exitMs)),
    avgPnl: summarize(group).avgPct,
  })).sort((a, b) => b.size - a.size);
}

function averageVec(vecs) {
  const n = vecs.length;
  return vecs[0].map((_, i) => sum(vecs.map((v) => v[i])) / n);
}

async function loadReceiptLanes() {
  const lanes = new Map();
  const inputHashes = new Set();
  const statusByLane = [];
  for (const specRaw of receiptLaneSpecs) {
    const spec = parseLaneSpec(specRaw);
    const lane = { spec, receipts: new Map(), statusCounts: new Map(), reasonCounts: new Map() };
    for await (const row of readJsonl(spec.file)) {
      if (rowTime(row) < sinceMs) continue;
      lane.statusCounts.set(row.status || "missing", (lane.statusCounts.get(row.status || "missing") || 0) + 1);
      for (const reason of row.reason_codes || []) {
        lane.reasonCounts.set(reason, (lane.reasonCounts.get(reason) || 0) + 1);
      }
      if (row.status !== "accepted" || !row.input_hash || !row.mint) continue;
      if (!lane.receipts.has(row.input_hash)) {
        lane.receipts.set(row.input_hash, row);
        inputHashes.add(row.input_hash);
      }
    }
    lanes.set(spec.name, lane);
    statusByLane.push({ lane: spec.name, file: spec.file, counts: lane.statusCounts, reasons: lane.reasonCounts });
  }
  return { lanes, inputHashes, statusByLane };
}

async function loadInputs(inputHashes) {
  const inputs = new Map();
  const dict = new Map();
  let sampled = 0;
  let total = 0;
  for await (const row of readJsonl(inputsFile)) {
    total += 1;
    const hash = crypto.createHash("sha256").update(row.__rawLine || JSON.stringify(row)).digest("hex");
    if (sampled < maxFeatureSamples) {
      recordFeaturePaths(row, "", dict, { totalRowsWithAnyField: 0 });
      sampled += 1;
    }
    if (hash && inputHashes.has(hash) && !inputs.has(hash)) {
      inputs.set(hash, row);
    }
  }
  return { inputs, dict, totalInputs: total, sampled };
}

async function loadMarks(mints) {
  const marks = new Map();
  let total = 0;
  let used = 0;
  for await (const row of readJsonl(capsulesFile)) {
    total += 1;
    const mint = row.candidate?.mint || row.mint || row.raw_record?.mint;
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
  return { marks, totalCapsules: total, usedMarks: used };
}

function buildRows(lanes, inputs, marks, liveAuditModels = new Map()) {
  const rows = [];
  for (const [laneName, lane] of lanes) {
    for (const receipt of lane.receipts.values()) {
      const input = inputs.get(receipt.input_hash);
      if (!input) continue;
      const entryAtMs = num(get(input, "provider.candidate_ready_ts_ms")) ?? rowTime(input);
      const featureCutoffTimestamp = entryAtMs;
      const features = hydrateFeatures(input, receipt);
      const theoreticalScore = scoreMarks(features.entry_mc_usd, entryAtMs, marks.get(receipt.mint), primaryTp, primarySl, primaryHoldMs);
      const liveAudit = liveAuditModels.get(receipt.mint);
      const auditMarkScore = liveAudit?.markScore;
      const observedLive = liveAudit?.observedLive;
      const executableScore = auditMarkScore?.outcome && auditMarkScore.outcome !== "missing"
        ? auditMarkScore
        : theoreticalScore;
      const executableSource = auditMarkScore?.outcome && auditMarkScore.outcome !== "missing"
        ? "live_watcher_audit_marks"
        : "capsule_mc_proxy";
      rows.push({
        lane: laneName,
        mint: receipt.mint,
        inputHash: receipt.input_hash,
        entryAtMs,
        featureCutoffTimestamp,
        sourceSlot: num(receipt.source_slot),
        features,
        theoreticalOutcome: theoreticalScore.outcome,
        theoreticalPnlPct: theoreticalScore.pnlPct,
        theoreticalExitMs: theoreticalScore.exitMs,
        theoreticalMaxRoiPct: theoreticalScore.maxRoiPct,
        theoreticalMinRoiPct: theoreticalScore.minRoiPct,
        executableModelOutcome: executableScore.outcome,
        executableModelPnlPct: executableScore.pnlPct,
        executableModelExitMs: executableScore.exitMs,
        executableModelMaxRoiPct: executableScore.maxRoiPct,
        executableModelMinRoiPct: executableScore.minRoiPct,
        executableModelSource: executableSource,
        observedLiveOutcome: observedLive?.outcome || null,
        observedLivePnlPct: observedLive?.pnlPct ?? null,
        observedLiveReason: observedLive?.reason || null,
        observedLiveExitMs: liveAudit?.exitDecisionAtMs != null ? liveAudit.exitDecisionAtMs - entryAtMs : null,
        outcome: executableScore.outcome,
        pnlPct: executableScore.pnlPct,
        exitMs: executableScore.exitMs,
        maxRoiPct: executableScore.maxRoiPct,
        minRoiPct: executableScore.minRoiPct,
        quality: qualityBucket(executableScore),
        marks: marks.get(receipt.mint)?.length || 0,
        liveAuditMarks: liveAudit?.marks?.length || 0,
      });
    }
  }
  return rows;
}

function dedupeRows(rows, laneName = "parent") {
  const out = new Map();
  for (const row of rows.filter((r) => r.lane === laneName).sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    if (!out.has(row.mint)) out.set(row.mint, row);
  }
  return [...out.values()];
}

async function loadLiveSanity() {
  const selected = readJsonlSync(selectedFile).filter((row) => rowTime(row) >= selectedSinceMs);
  const audits = readJsonlSync(auditFile).filter((row) => rowTime(row) >= selectedSinceMs);
  liveSanityStats = {
    selected: selected.length,
    audits: audits.length,
    exitDecisions: audits.filter((row) => row.kind === "rustExitDecision").length,
    positiveExitDecisions: audits.filter((row) => row.kind === "rustExitDecision" && [row.pnlPct, row.feeAdjustedPnlPct, row.grossPnlPct].map((value) => num(value)).some((value) => value > 0)).length,
  };
  const byMint = new Map();
  for (const row of audits) {
    const mint = rowMint(row);
    if (!mint) continue;
    const s = byMint.get(mint) || {
      mint,
      rows: [],
      buyStart: null,
      buy: null,
      finality: null,
      exactProof: null,
      streamingExactProof: null,
      exitMarks: [],
      exitDecision: null,
      sellQuoteRefresh: null,
      sellBroadcast: null,
      sellFinality: null,
    };
    s.rows.push(row);
    if (row.kind === "rustSelectedCandidateBuyStart" && !s.buyStart) s.buyStart = row;
    if (row.kind === "rustSelectedCandidateBuyResult" && !s.buy) s.buy = row;
    if (row.kind === "rustSelectedCandidateBuyFinality" && !s.finality) s.finality = row;
    if (row.kind === "rustSelectedCandidateExactBalanceProof" && !s.exactProof) s.exactProof = row;
    if (row.kind === "rustSelectedCandidateStreamingExactBalanceActivation" && !s.streamingExactProof) s.streamingExactProof = row;
    if (row.kind === "rustExitMark" && Number.isFinite(liveDecisionPnl(row))) {
      s.exitMarks.push({
        tsMs: rowTime(row),
        pnlPct: liveDecisionPnl(row),
        markSource: row.markSource || null,
        expectedSolOutLamports: num(row.expectedSolOutLamports) ?? num(row.executableSolOutLamports),
        netSolOutLamports: num(row.netSolOutLamports),
        tokensHeldRaw: row.tokensHeldRaw || row.positionTokenAmountRaw || null,
        remainingCostLamports: num(row.remainingCostLamports),
      });
    }
    if (row.kind === "rustExitDecision") s.exitDecision = row;
    if (row.kind === "rustExitSendTimeQuoteRefresh") s.sellQuoteRefresh = row;
    if (row.kind === "rustExitSellBroadcast") s.sellBroadcast = row;
    if (row.kind === "rustExitSellFinality") s.sellFinality = row;
    byMint.set(mint, s);
  }
  const selectedByMint = new Map();
  for (const row of selected.sort((a, b) => rowTime(a) - rowTime(b))) {
    if (row.mint && !selectedByMint.has(row.mint)) selectedByMint.set(row.mint, row);
  }
  const liveAuditModels = new Map();
  const liveRows = [];
  for (const [mint, s] of byMint) {
    s.rows.sort((a, b) => rowTime(a) - rowTime(b));
    s.exitMarks.sort((a, b) => a.tsMs - b.tsMs);
    const selectedRow = selectedByMint.get(mint);
    const selectedAtMs = rowTime(selectedRow);
    const executableEntryAtMs = rowTime(s.buyStart) || rowTime(s.buy);
    const entryAtMs = Number.isFinite(executableEntryAtMs) ? executableEntryAtMs : selectedAtMs;
    const markScore = scorePnlMarks(entryAtMs, s.exitMarks, primaryTp, primarySl, primaryHoldMs);
    const observedLive = s.exitDecision ? labelFromLiveDecision(s.exitDecision) : null;
    const exitDecisionAtMs = rowTime(s.exitDecision);
    const exactProofAtMs = rowTime(s.exactProof);
    const streamingExactProofAtMs = rowTime(s.streamingExactProof);
    const liveModel = {
      mint,
      selectedRow,
      selectedAtMs,
      executableEntryAtMs,
      buyStart: s.buyStart,
      buy: s.buy,
      finality: s.finality,
      exactProof: s.exactProof,
      streamingExactProof: s.streamingExactProof,
      marks: s.exitMarks,
      markScore,
      observedLive,
      exitDecision: s.exitDecision,
      sellQuoteRefresh: s.sellQuoteRefresh,
      sellBroadcast: s.sellBroadcast,
      sellFinality: s.sellFinality,
      exitDecisionAtMs: Number.isFinite(exitDecisionAtMs) ? exitDecisionAtMs : null,
      exactProofLatencyMs: Number.isFinite(exactProofAtMs) && Number.isFinite(entryAtMs) ? exactProofAtMs - entryAtMs : null,
      streamingExactProofLatencyMs: Number.isFinite(streamingExactProofAtMs) && Number.isFinite(entryAtMs) ? streamingExactProofAtMs - entryAtMs : null,
    };
    liveAuditModels.set(mint, liveModel);
    if (s.exitDecision && observedLive) {
      liveRows.push({
        mint,
        livePnlPct: observedLive.pnlPct,
        liveExitReason: observedLive.reason,
        observedLiveOutcome: observedLive.outcome,
        selected: selectedByMint.has(mint),
        selectedMc: num(selectedRow?.entryMarketCapUsd),
        selectedAtMs,
        executableEntryAtMs,
        firstSellableAtMs: Number.isFinite(streamingExactProofAtMs) ? streamingExactProofAtMs : exactProofAtMs,
        buyOk: s.buy?.ok,
        finalityError: s.finality?.error || s.finality?.finalityError || null,
        buyMs: num(s.buy?.selectedToBuyResultMs),
        finalGuardMc: num(s.buy?.finalEconomicGuardEntryMarketCapUsd),
        finalGuardSource: s.buy?.finalEconomicGuardReserveSource || s.buy?.preSignReserveSource || null,
        watcherMarks: s.exitMarks.length,
        watcherMarkOutcome: markScore.outcome,
        watcherMarkPnlPct: markScore.pnlPct,
        watcherMarkExitMs: markScore.exitMs,
        exactProofLatencyMs: liveModel.exactProofLatencyMs,
        streamingExactProofLatencyMs: liveModel.streamingExactProofLatencyMs,
        expectedSolOutLamports: num(s.exitDecision?.expectedSolOutLamports) ?? num(s.sellBroadcast?.expectedSolOutLamports),
        sellStatus: s.sellFinality?.status || null,
        _auditRows: audits.length,
      });
    }
  }
  return { liveRows, liveAuditModels };
}

function readJsonlSync(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8")
    .split(/\r?\n/)
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

function oldFingerprintTable(rowsByLane) {
  const out = [];
  for (const [lane, rows] of rowsByLane) {
    out.push(summaryRow(lane, rows));
  }
  return out.sort((a, b) => b.trades - a.trades);
}

function featureDictionary(dict, rows) {
  const featureRows = featureSpecs.map((spec) => {
    const vals = rows.map((row) => featureValue(row, spec.name)).filter(Number.isFinite);
    return {
      feature: spec.name,
      source: spec.source,
      definition: spec.path,
      units: spec.units,
      available: "yes",
      missing: rows.length - vals.length,
      coverage: pct(vals.length, rows.length),
      leak: spec.leak,
    };
  });
  const discovered = [...dict.values()].sort((a, b) => b.count - a.count).slice(0, 35).map((item) => ({
    feature: item.path,
    source: "decision-inputs discovered path",
    definition: item.path,
    units: [...item.typeCounts.keys()].join("/"),
    available: "yes",
    missing: "sampled",
    coverage: "sampled",
    leak: item.path.includes("outcome") || item.path.includes("future") ? "review" : "none/infra",
  }));
  return { featureRows, discovered };
}

function simulatePolicies(rows) {
  const out = [];
  for (const tp of tpPcts) {
    for (const sl of slPcts) {
      for (const hold of holdMsList) {
        const scored = rows.map((row) => {
          const score = scoreMarks(featureValue(row, "entry_mc_usd"), row.entryAtMs, row._marks, tp, sl, hold);
          return { ...row, outcome: score.outcome, pnlPct: score.pnlPct ?? 0, exitMs: score.exitMs ?? hold };
        }).filter((row) => Number.isFinite(row.pnlPct));
        if (scored.length < minPocketSample) continue;
        const s = summarize(scored);
        out.push({ tp, sl, hold, ...s, maxDd: maxDrawdown(scored), losingStreak: losingStreak(scored) });
      }
    }
  }
  return out.sort((a, b) => b.pf - a.pf || b.netPct - a.netPct).slice(0, 20);
}

function attachMarks(rows, marks) {
  return rows.map((row) => ({ ...row, _marks: marks.get(row.mint) || [] }));
}

function modelValidationRows(rows) {
  return rows
    .filter((row) => row.lane === "parent" && row.observedLiveOutcome)
    .sort((a, b) => a.entryAtMs - b.entryAtMs);
}

function validationMetrics(rows, predictionOutcomeField, predictionPnlField, label) {
  const complete = rows.filter((row) => row[predictionOutcomeField] && row.observedLiveOutcome && Number.isFinite(num(row[predictionPnlField])) && Number.isFinite(num(row.observedLivePnlPct)));
  const errors = complete.map((row) => Math.abs(num(row[predictionPnlField]) - num(row.observedLivePnlPct)));
  const matches = complete.filter((row) => row[predictionOutcomeField] === row.observedLiveOutcome).length;
  const predTp = complete.filter((row) => row[predictionOutcomeField] === "TP");
  const obsTp = complete.filter((row) => row.observedLiveOutcome === "TP");
  const predSl = complete.filter((row) => row[predictionOutcomeField] === "SL");
  const obsSl = complete.filter((row) => row.observedLiveOutcome === "SL");
  const tpTrue = predTp.filter((row) => row.observedLiveOutcome === "TP").length;
  const slTrue = predSl.filter((row) => row.observedLiveOutcome === "SL").length;
  return {
    label,
    rows: complete.length,
    accuracy: pct(matches, complete.length),
    tpPrecision: pct(tpTrue, predTp.length),
    tpRecall: pct(tpTrue, obsTp.length),
    slPrecision: pct(slTrue, predSl.length),
    slRecall: pct(slTrue, obsSl.length),
    meanAbsPnlError: errors.length ? sum(errors) / errors.length : null,
    medianAbsPnlError: median(errors),
    p90AbsPnlError: quantile(errors, 0.9),
  };
}

function labelTransitionSummary(rows) {
  const out = new Map();
  for (const row of rows) {
    const key = `${row.theoreticalOutcome}->${row.executableModelOutcome}->${row.observedLiveOutcome || "no_live"}`;
    out.set(key, (out.get(key) || 0) + 1);
  }
  return [...out.entries()].map(([transition, count]) => ({ transition, count })).sort((a, b) => b.count - a.count);
}

function buildReport({ lanes, inputs, dict, totalInputs, sampled, marksResult, liveWinners, rows }) {
  const rowsByLane = new Map();
  for (const [laneName] of lanes) {
    rowsByLane.set(laneName, dedupeRows(rows, laneName));
  }
  const parent = rowsByLane.get("parent") || [...rowsByLane.values()][0] || [];
  const parentByMint = new Map(parent.map((row) => [row.mint, row]));
  const liveWinnerRows = liveWinners.map((row) => {
    const executable = parentByMint.get(row.mint);
    return {
      ...row,
      executableRepresented: Boolean(executable),
      theoreticalOutcome: executable?.theoreticalOutcome || "missing",
      theoreticalPnlPct: executable?.theoreticalPnlPct ?? null,
      executableOutcome: executable?.executableModelOutcome || "missing",
      executablePnlPct: executable?.executableModelPnlPct ?? null,
      executableSource: executable?.executableModelSource || "missing",
      executableMarks: executable?.marks ?? 0,
      liveAuditMarks: executable?.liveAuditMarks ?? row.watcherMarks ?? 0,
    };
  });
  const validationRows = modelValidationRows(parent);
  const validationSummary = [
    validationMetrics(validationRows, "theoreticalOutcome", "theoreticalPnlPct", "THEORETICAL_LABEL vs OBSERVED_LIVE_LABEL"),
    validationMetrics(validationRows, "executableModelOutcome", "executableModelPnlPct", "EXECUTABLE_MODEL_LABEL vs OBSERVED_LIVE_LABEL"),
  ];
  const sanityRows = liveWinnerRows
    .filter((row) => sanityFixtureMints.has(row.mint))
    .concat(liveWinnerRows.filter((row) => !sanityFixtureMints.has(row.mint)).slice(0, 8));
  const parentWithMarks = attachMarks(parent, marksResult.marks);
  const split = trainValidation(parent);
  const featureDict = featureDictionary(dict, parent);
  const featureStats = featureSpecs.map((spec) => numericStats(parent, spec)).filter(Boolean)
    .sort((a, b) => Math.abs(b.effect || 0) - Math.abs(a.effect || 0));
  const bins = featureSpecs.flatMap((spec) => featureBins(parent, spec))
    .sort((a, b) => b.pf - a.pf || b.netPct - a.netPct)
    .slice(0, 40);
  const pocketsAll = pocketRows(parent);
  const pocketsTrain = pocketRows(split.train);
  const pocketsValidation = pocketsTrain.map((p) => {
    const defName = p.name;
    const subset = applyPocketByName(split.validation, defName);
    return { name: defName, trainTrades: p.trades, trainPF: p.pf, trainNet: p.netPct, ...summarize(subset) };
  }).filter((row) => row.trades >= Math.max(5, Math.floor(minPocketSample / 3)))
    .sort((a, b) => b.pf - a.pf || b.netPct - a.netPct);
  const nn = nearestNeighborReport(parent);
  const clusters = clusterWinners(parent);
  const avoid = featureBins(parent.filter((row) => row.outcome === "SL" || row.quality === "E_fast_sl" || row.pnlPct < -10), featureSpecs.find((s) => s.name === "entry_mc_usd") || featureSpecs[0]);
  const policyGrid = simulatePolicies(parentWithMarks);
  const capacity = [1, 2, 3, 4].map((n) => capacityReplay(parent, n));

  console.log("# Historical Executable Fingerprint / Simulation Rebuild");
  console.log("");
  console.log("Read-only analytics only. No live service changes, no threshold changes, no restarts.");
  console.log("");

  console.log("## 0. Label Sanity / Live Winner Reconciliation");
  console.log("");
  console.log(`Live exits since ${new Date(selectedSinceMs).toISOString()}: ${liveWinnerRows.length}`);
  console.log(`Phase 0 scan rows: selected=${liveSanityStats.selected}, audit=${liveSanityStats.audits}, exitDecisions=${liveSanityStats.exitDecisions}, positiveExitDecisions=${liveSanityStats.positiveExitDecisions}`);
  console.log(markdownTable(sanityRows, [
    { label: "Mint", value: (r) => `\`${r.mint}\`` },
    { label: "Observed live", value: (r) => `${r.observedLiveOutcome || "n/a"} ${fmtPct(r.livePnlPct)}` },
    { label: "Selected row?", value: (r) => r.selected },
    { label: "Selected MC", value: (r) => r.selectedMc == null ? "n/a" : `$${fmt(r.selectedMc, 2)}` },
    { label: "Guard MC", value: (r) => r.finalGuardMc == null ? "n/a" : `$${fmt(r.finalGuardMc, 2)}` },
    { label: "Guard source", value: (r) => r.finalGuardSource || "n/a" },
    { label: "Buy ms", value: (r) => r.buyMs ?? "n/a" },
    { label: "Executable row?", value: (r) => r.executableRepresented },
    { label: "Theoretical label", value: (r) => `${r.theoreticalOutcome} ${fmtPct(r.theoreticalPnlPct)}` },
    { label: "Executable model", value: (r) => `${r.executableOutcome} ${fmtPct(r.executablePnlPct)}` },
    { label: "Model source", value: (r) => r.executableSource },
    { label: "Capsule marks", value: (r) => r.executableMarks },
    { label: "Live marks", value: (r) => r.liveAuditMarks },
  ]));
  console.log("");
  console.log("Root cause: the old historical labeler used capsule market-cap marks as the executable target label. Live exits use watcher sell-quote economics from actual held token quantity/cost basis. Those are different economic objects, so capsule MC can falsely order SL/TP or miss TP entirely.");
  console.log("");
  console.log("## 0B. Live Model Validation");
  console.log("");
  console.log(markdownTable(validationSummary, [
    { label: "Comparison", value: (r) => r.label },
    { label: "Rows", value: (r) => r.rows },
    { label: "Accuracy", value: (r) => fmtPct(r.accuracy) },
    { label: "TP precision", value: (r) => fmtPct(r.tpPrecision) },
    { label: "TP recall", value: (r) => fmtPct(r.tpRecall) },
    { label: "SL precision", value: (r) => fmtPct(r.slPrecision) },
    { label: "SL recall", value: (r) => fmtPct(r.slRecall) },
    { label: "Mean abs PnL err", value: (r) => fmtPct(r.meanAbsPnlError) },
    { label: "Median abs PnL err", value: (r) => fmtPct(r.medianAbsPnlError) },
    { label: "P90 abs PnL err", value: (r) => fmtPct(r.p90AbsPnlError) },
  ]));
  console.log("");
  console.log("## 0C. Label Transition Audit");
  console.log("");
  console.log(markdownTable(labelTransitionSummary(parent).slice(0, 15), [
    { label: "Theoretical -> Executable -> Observed", value: (r) => r.transition },
    { label: "Count", value: (r) => r.count },
  ]));
  console.log("");

  console.log("## 1. Dataset Inventory");
  console.log("");
  const inventory = [...lanes.values()].map((lane) => ({
    lane: lane.spec.name,
    file: lane.spec.file,
    accepted: lane.receipts.size,
    statuses: [...lane.statusCounts.entries()].map(([k, v]) => `${k}:${v}`).join(", "),
  }));
  console.log(markdownTable(inventory, [
    { label: "Dataset/Lane", value: (r) => r.lane },
    { label: "Accepted rows", value: (r) => r.accepted },
    { label: "Status counts", value: (r) => r.statuses },
  ]));
  console.log("");
  console.log(`Decision inputs scanned: ${totalInputs}; sampled for dictionary: ${sampled}; hydrated accepted inputs: ${inputs.size}`);
  console.log(`Capsules scanned: ${marksResult.totalCapsules}; forward marks used: ${marksResult.usedMarks}; mints with marks: ${marksResult.marks.size}`);
  console.log("");

  console.log("## 2. Feature Dictionary");
  console.log("");
  console.log(markdownTable(featureDict.featureRows, [
    { label: "Feature", value: (r) => r.feature },
    { label: "Source", value: (r) => r.source },
    { label: "Units", value: (r) => r.units },
    { label: "Available at decision?", value: (r) => r.available },
    { label: "Missing", value: (r) => r.missing },
    { label: "Coverage", value: (r) => fmtPct(r.coverage) },
    { label: "Leakage risk", value: (r) => r.leak },
  ]));
  console.log("");

  console.log("## 3. Leakage Audit");
  console.log("");
  console.log("- Every predictive feature in this report is hydrated from `decision-inputs.jsonl` or receipt fields with `feature_cutoff_timestamp = provider.candidate_ready_ts_ms`.");
  console.log("- `THEORETICAL_LABEL` is the old capsule/market-cap proxy: entry MC versus future capsule MC marks, with flat round-trip cost.");
  console.log("- `EXECUTABLE_MODEL_LABEL` uses live watcher economic marks where available; otherwise it falls back to the capsule proxy and is marked as `capsule_mc_proxy`.");
  console.log("- `OBSERVED_LIVE_LABEL` is actual live exit decision/result where available. It is validation truth only, not a predictive feature.");
  console.log("- Live audit exits are used only for Phase 0 model validation and live-trade fixtures, not for broad candidate feature mining.");
  console.log("- Remaining leakage risk: receipt `accepted` itself is an executable label gate, so strategy mining must not use unavailable/rejected status as alpha unless explicitly modeling executability.");
  console.log("");

  console.log("## 4. Old Shadow / Fingerprint Reproduction Under Executable Labels");
  console.log("");
  console.log(markdownTable(oldFingerprintTable(rowsByLane), [
    { label: "Fingerprint/Lane", value: (r) => r.name },
    { label: "Executable sample", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "Net %", value: (r) => fmtPct(r.netPct) },
    { label: "Net SOL @1SOL", value: (r) => fmtSolPct(r.netPct) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Median", value: (r) => fmtPct(r.medianPct) },
  ]));
  console.log("");

  console.log("## 5. Executable Relabeling / Quality Buckets");
  console.log("");
  const qualityRows = [...groupCount(parent, (r) => r.quality).entries()].map(([quality, count]) => ({
    quality,
    count,
    pct: pct(count, parent.length),
    avgPnl: summarize(parent.filter((row) => row.quality === quality)).avgPct,
  })).sort((a, b) => b.count - a.count);
  console.log(markdownTable(qualityRows, [
    { label: "Quality", value: (r) => r.quality },
    { label: "Count", value: (r) => r.count },
    { label: "Share", value: (r) => fmtPct(r.pct) },
    { label: "Avg PnL", value: (r) => fmtPct(r.avgPnl) },
  ]));
  console.log("");

  console.log("## 6. Winner Vs Loser Fingerprints");
  console.log("");
  console.log(markdownTable(featureStats.slice(0, 25), [
    { label: "Feature", value: (r) => r.feature },
    { label: "Coverage", value: (r) => fmtPct(r.coverage) },
    { label: "All median", value: (r) => fmt(r.median, 4) },
    { label: "Winner median", value: (r) => fmt(r.winnerMedian, 4) },
    { label: "Loser median", value: (r) => fmt(r.loserMedian, 4) },
    { label: "Effect", value: (r) => fmt(r.effect, 3) },
  ]));
  console.log("");

  console.log("## 7. Strong-Winner Fingerprints");
  console.log("");
  const strong = parent.filter((row) => row.quality === "A_strong_winner" || row.maxRoiPct >= 40);
  console.log(markdownTable(featureSpecs.map((spec) => numericStats(strong, spec)).filter(Boolean).slice(0, 20), [
    { label: "Feature", value: (r) => r.feature },
    { label: "Count", value: (r) => r.count },
    { label: "Median", value: (r) => fmt(r.median, 4) },
    { label: "P25", value: (r) => fmt(r.p25, 4) },
    { label: "P75", value: (r) => fmt(r.p75, 4) },
  ]));
  console.log("");

  console.log("## 8. Fast-Loser / Avoid Fingerprints");
  console.log("");
  console.log(markdownTable(avoid.slice(0, 15), [
    { label: "Feature", value: (r) => r.feature },
    { label: "Range", value: (r) => r.range },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "Net", value: (r) => fmtPct(r.netPct) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
  ]));
  console.log("");

  console.log("## 9. Feature Interactions / Profitable Pockets");
  console.log("");
  console.log(markdownTable(pocketsAll.slice(0, 20), [
    { label: "Pocket", value: (r) => r.name },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "Net %", value: (r) => fmtPct(r.netPct) },
    { label: "Net SOL @1SOL", value: (r) => fmtSolPct(r.netPct) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "Worst", value: (r) => fmtPct(r.worst) },
  ]));
  console.log("");

  console.log("## 10. Percentile/Bin Results");
  console.log("");
  console.log(markdownTable(bins.slice(0, 30), [
    { label: "Feature", value: (r) => r.feature },
    { label: "Range", value: (r) => r.range },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "Net", value: (r) => fmtPct(r.netPct) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
  ]));
  console.log("");

  console.log("## 11. Similarity / Nearest-Neighbor Results");
  console.log("");
  console.log(markdownTable(nn, [
    { label: "K", value: (r) => r.k },
    { label: "Validation rows", value: (r) => r.validationRows },
    { label: "Selected by NN", value: (r) => r.selectedByNeighbor },
    { label: "Selected WR", value: (r) => fmtPct(r.selectedWinRate) },
    { label: "Selected avg", value: (r) => fmtPct(r.selectedAvgPnl) },
    { label: "Selected PF", value: (r) => Number.isFinite(r.selectedPF) ? fmt(r.selectedPF, 2) : "inf" },
  ]));
  console.log("");

  console.log("## 12. Winner Clusters");
  console.log("");
  console.log(markdownTable(clusters, [
    { label: "Cluster", value: (r) => r.cluster },
    { label: "Size", value: (r) => r.size },
    { label: "Median MC", value: (r) => fmt(r.medianMc, 2) },
    { label: "Median age", value: (r) => fmt(r.medianAge, 1) },
    { label: "Buy1000", value: (r) => fmt(r.medianBuy1000, 2) },
    { label: "Net buy", value: (r) => fmt(r.medianNetBuy, 3) },
    { label: "Dom", value: (r) => fmt(r.medianDom, 3) },
    { label: "FB bps", value: (r) => fmt(r.medianFirstBuyerBps, 1) },
    { label: "Time to TP", value: (r) => r.medianTimeToTp == null ? "n/a" : `${fmt(r.medianTimeToTp, 0)}ms` },
  ]));
  console.log("");

  console.log("## 13. Temporal / Opportunity-Duration Fingerprints");
  console.log("");
  const opportunity = parent.filter((row) => row.outcome === "TP");
  console.log(markdownTable([{
    winners: opportunity.length,
    medianTimeToTp: median(opportunity.map((row) => row.exitMs)),
    p25TimeToTp: quantile(opportunity.map((row) => row.exitMs), 0.25),
    p75TimeToTp: quantile(opportunity.map((row) => row.exitMs), 0.75),
    medianPeakAt: median(opportunity.map((row) => row.peakAtMs)),
  }], [
    { label: "Executable winners", value: (r) => r.winners },
    { label: "Median time to TP", value: (r) => r.medianTimeToTp == null ? "n/a" : `${fmt(r.medianTimeToTp, 0)}ms` },
    { label: "P25", value: (r) => r.p25TimeToTp == null ? "n/a" : `${fmt(r.p25TimeToTp, 0)}ms` },
    { label: "P75", value: (r) => r.p75TimeToTp == null ? "n/a" : `${fmt(r.p75TimeToTp, 0)}ms` },
    { label: "Median peak at", value: (r) => r.medianPeakAt == null ? "n/a" : `${fmt(r.medianPeakAt, 0)}ms` },
  ]));
  console.log("");

  console.log("## 14. TP/SL/Hold Simulation");
  console.log("");
  console.log(markdownTable(policyGrid.slice(0, 20), [
    { label: "TP", value: (r) => fmtPct(r.tp) },
    { label: "SL", value: (r) => fmtPct(r.sl) },
    { label: "Hold", value: (r) => `${r.hold / 1000}s` },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "Net", value: (r) => fmtPct(r.netPct) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
    { label: "DD", value: (r) => fmtPct(r.maxDd) },
    { label: "Loss streak", value: (r) => r.losingStreak },
  ]));
  console.log("");

  console.log("## 15. MAX_OPEN Simulation");
  console.log("");
  console.log(markdownTable(capacity, [
    { label: "Max open", value: (r) => r.maxOpen },
    { label: "Taken", value: (r) => r.trades },
    { label: "Blocked", value: (r) => r.blocked },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "Net", value: (r) => fmtPct(r.netPct) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
  ]));
  console.log("");

  console.log("## 16. Chronological Train / Validation");
  console.log("");
  console.log(markdownTable([
    summaryRow("train_70pct", split.train),
    summaryRow("validation_30pct", split.validation),
    ...pocketsValidation.slice(0, 10),
  ], [
    { label: "Set/Pocket", value: (r) => r.name },
    { label: "Trades", value: (r) => r.trades },
    { label: "WR", value: (r) => fmtPct(r.winRate) },
    { label: "Net", value: (r) => fmtPct(r.netPct) },
    { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
  ]));
  console.log("");

  console.log("## 17. Robustness / Overfit Analysis");
  console.log("");
  console.log("- Treat any pocket with fewer than " + minPocketSample + " executable rows as research-only.");
  console.log("- Promote only pockets that remain positive in chronological validation and are not hypersensitive to one exact threshold.");
  console.log("- The next script iteration should perturb the top validation pockets by +/-10-20% thresholds and report collapse/no-collapse.");
  console.log("");

  console.log("## 18. Best Strategy Candidates / New Executable Benchmark");
  console.log("");
  const bestValidation = pocketsValidation[0];
  console.log(bestValidation
    ? `Best current validation candidate: ${bestValidation.name}, validation trades=${bestValidation.trades}, WR=${fmtPct(bestValidation.winRate)}, net=${fmtPct(bestValidation.netPct)}, PF=${Number.isFinite(bestValidation.pf) ? fmt(bestValidation.pf, 2) : "inf"}.`
    : "No validation pocket currently clears minimum sample requirements.");
  console.log("");

  console.log("## Direct Answers");
  console.log("");
  console.log("A. Why did the old labeler call actual live TP winners SL/none? Because it used capsule MC proxy labels instead of the live watcher's held-token sell-quote PnL stream. Section 0 shows both labels side-by-side.");
  console.log("B. Does `fast_burst_buy250_ge_3` still hold up? See section 9 and section 16 after corrected executable labels; do not promote from provisional in-sample PF.");
  console.log("C. Which fingerprints survive realistic economics? See sections 4, 9, and 16; rows with `capsule_mc_proxy` still need validation before promotion.");
  console.log("D. Is the old MC-centric strategy justified? Only if it survives executable labels, chronological validation, and capacity; old theoretical MC-only wins are not enough.");
  console.log("E. Best stable OOS executable expectancy? Use section 16 plus model-validation section 0B; if validation accuracy is poor, the honest answer remains not ready.");
}

function applyPocketByName(rows, name) {
  return rows.filter((r) => {
    const mc = featureValue(r, "entry_mc_usd");
    const dom = featureValue(r, "dominant_buy_share_60");
    const fb = featureValue(r, "first_buyer_quote_share_bps");
    const b1000 = featureValue(r, "buy_count_1000ms");
    const sp = featureValue(r, "sell_pressure_60");
    const net = featureValue(r, "net_buy_sol_60");
    const b100 = featureValue(r, "buy_count_100ms");
    const b250 = featureValue(r, "buy_count_250ms");
    const uw = featureValue(r, "unique_wallets_60");
    if (name === "mc_le_5000") return mc <= 5000;
    if (name === "mc_le_5000_dom_le_055") return mc <= 5000 && dom <= 0.55;
    if (name === "mc_le_5000_fb_le_5000") return mc <= 5000 && fb <= 5000;
    if (name === "mc_le_5000_buy1000_ge_3") return mc <= 5000 && b1000 >= 3;
    if (name === "mc_le_5000_buy1000_ge_3_dom_le_055") return mc <= 5000 && b1000 >= 3 && dom <= 0.55;
    if (name === "mc_3500_5000_buy1000_ge_3") return between(mc, 3500, 5000) && b1000 >= 3;
    if (name === "low_sell_pressure_le_010") return sp <= 0.10;
    if (name === "strong_net_buy_ge_1") return net >= 1;
    if (name === "strong_net_buy_ge_2") return net >= 2;
    if (name === "fast_burst_buy100_ge_2") return b100 >= 2;
    if (name === "fast_burst_buy250_ge_3") return b250 >= 3;
    if (name === "broad_unique_ge_3") return uw >= 3;
    return false;
  });
}

function groupCount(rows, keyFn) {
  const out = new Map();
  for (const row of rows) {
    const key = keyFn(row);
    out.set(key, (out.get(key) || 0) + 1);
  }
  return out;
}

async function main() {
  const receiptResult = await loadReceiptLanes();
  const inputResult = await loadInputs(receiptResult.inputHashes);
  const mints = new Set();
  for (const lane of receiptResult.lanes.values()) {
    for (const receipt of lane.receipts.values()) mints.add(receipt.mint);
  }
  const marksResult = await loadMarks(mints);
  const liveSanity = await loadLiveSanity();
  const rows = buildRows(receiptResult.lanes, inputResult.inputs, marksResult.marks, liveSanity.liveAuditModels);
  buildReport({
    lanes: receiptResult.lanes,
    inputs: inputResult.inputs,
    dict: inputResult.dict,
    totalInputs: inputResult.totalInputs,
    sampled: inputResult.sampled,
    marksResult,
    liveWinners: liveSanity.liveRows,
    rows,
  });
}

main().catch((err) => {
  console.error(err?.stack || err);
  process.exit(1);
});
