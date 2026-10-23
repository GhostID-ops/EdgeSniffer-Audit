#!/usr/bin/env node
import fs from "node:fs";
import readline from "node:readline";

const baseDir = process.env.TEACHER_IMPULSE_BASE_DIR
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1";
const selectedFile = process.env.TEACHER_IMPULSE_SELECTED_EVENT_OUTPUT
  || `${baseDir}/live-mc5000-selected-events.jsonl`;
const auditFile = process.env.RUST_SELECTED_CANDIDATE_AUDIT_FILE
  || `${baseDir}/live-mc5000-dust/rust-selected-candidate-audit.jsonl`;
const capsulesFile = process.env.TEACHER_IMPULSE_CAPSULES_FILE
  || `${baseDir}/candidate-capsules.jsonl`;
const sinceMs = Date.parse(process.env.REPORT_SINCE || "2026-08-29T23:06:27Z");
const policy = process.env.REPORT_POLICY || "teacher_impulse_mc5000_tp20_sl20_landing_calibration_v2";
const waits = String(process.env.RECOVERY_WAITS_MS || "10,25,50,75,100,150,250,500")
  .split(",")
  .map((x) => Number(x.trim()))
  .filter(Number.isFinite);
const maxEntryMcUsd = Number(process.env.MAX_ENTRY_MC_USD || 5000);
const maxCapsuleMcUsd = Number(process.env.MAX_CAPSULE_MC_USD || 500000);
const tpPct = Number(process.env.TP_PCT || 20);
const slPct = Number(process.env.SL_PCT || -20);
const holdMs = Number(process.env.HOLD_MS || 120000);
const costBps = Number(process.env.ROUND_TRIP_COST_BPS || 250);

function num(value, fallback = null) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function atMs(row) {
  return num(row?.atMs)
    ?? num(row?.entryAtMs)
    ?? num(row?.decidedAtMs)
    ?? num(row?.candidate_ready_ts_ms)
    ?? num(row?.local_receive_ts_ms)
    ?? num(row?.canonical_state?.observed_at_ms)
    ?? Date.parse(row?.at || "");
}

function rowMint(row) {
  return row?.mint || row?.extra?.mint || row?.candidate?.mint || row?.raw_record?.mint || null;
}

function rowKey(row) {
  return row?.key || row?.selectedKey || row?.candidateKey || row?.extra?.key || null;
}

async function* readJsonl(file) {
  if (!fs.existsSync(file)) return;
  const rl = readline.createInterface({ input: fs.createReadStream(file), crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line.trim()) continue;
    try {
      yield JSON.parse(line);
    } catch {
      // best-effort read-only audit
    }
  }
}

function pct(n, d) {
  return d ? (n / d) * 100 : 0;
}

function fmt(v, digits = 2) {
  return Number.isFinite(v) ? Number(v.toFixed(digits)) : null;
}

function fmtPct(v) {
  return Number.isFinite(v) ? `${fmt(v, 2)}%` : "n/a";
}

function sum(xs) {
  return xs.filter(Number.isFinite).reduce((a, b) => a + b, 0);
}

function pf(pnls) {
  const gp = sum(pnls.filter((v) => v > 0));
  const gl = Math.abs(sum(pnls.filter((v) => v < 0)));
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

function score(entryMc, entryAt, marks) {
  if (!Number.isFinite(entryMc) || !marks?.length) return { outcome: "missing", pnlPct: null, exitMs: null };
  const costPct = costBps / 100;
  let last = null;
  let firstTp = null;
  let firstSl = null;
  let maxRoi = -Infinity;
  for (const mark of marks) {
    const dt = mark.at - entryAt;
    if (dt < 0 || dt > holdMs) continue;
    const roi = ((mark.mc / entryMc) - 1) * 100 - costPct;
    maxRoi = Math.max(maxRoi, roi);
    last = { dt, roi };
    if (!firstTp && roi >= tpPct) firstTp = { dt, roi };
    if (!firstSl && roi <= slPct) firstSl = { dt, roi };
  }
  if (firstTp && (!firstSl || firstTp.dt <= firstSl.dt)) return { outcome: "TP", pnlPct: tpPct, exitMs: firstTp.dt, maxRoiPct: maxRoi };
  if (firstSl) return { outcome: "SL", pnlPct: slPct, exitMs: firstSl.dt, maxRoiPct: maxRoi };
  return { outcome: "none", pnlPct: last?.roi ?? null, exitMs: last?.dt ?? null, maxRoiPct: Number.isFinite(maxRoi) ? maxRoi : null };
}

const selected = [];
for await (const row of readJsonl(selectedFile)) {
  if (atMs(row) >= sinceMs && (!policy || row.policy === policy)) selected.push(row);
}

const audits = [];
for await (const row of readJsonl(auditFile)) {
  if (atMs(row) >= sinceMs) audits.push(row);
}

const selectedByKey = new Map();
const selectedByMint = new Map();
for (const row of selected) {
  const key = rowKey(row);
  const mint = rowMint(row);
  if (key && !selectedByKey.has(key)) selectedByKey.set(key, row);
  if (mint && !selectedByMint.has(mint)) selectedByMint.set(mint, row);
}

const cacheMissRejects = audits.filter((row) =>
  row.kind === "rustSelectedCandidateRejected"
  && String(row.reason || row.rejectReason || "").includes("fill_first_send_time_curve_cache_miss")
);
const cacheMisses = cacheMissRejects.map((row) => {
  const key = rowKey(row);
  const mint = rowMint(row);
  const selectedRow = (key && selectedByKey.get(key)) || (mint && selectedByMint.get(mint));
  return {
    key,
    mint,
    selectedAt: atMs(selectedRow || row),
    selectedMc: num(selectedRow?.entryMarketCapUsd),
    rejectAt: atMs(row),
    reason: row.reason || row.rejectReason,
  };
}).filter((row) => row.mint && Number.isFinite(row.selectedAt));

const neededMints = new Set(cacheMisses.map((row) => row.mint));
const marksByMint = new Map();
for await (const row of readJsonl(capsulesFile)) {
  const mint = rowMint(row);
  if (!mint || !neededMints.has(mint)) continue;
  const at = atMs(row);
  const mc = num(row?.canonical_state?.market_cap_usd) ?? num(row?.candidate?.market_cap_usd);
  if (!Number.isFinite(at) || !Number.isFinite(mc) || mc <= 0 || mc > maxCapsuleMcUsd) continue;
  const arr = marksByMint.get(mint) || [];
  arr.push({ at, mc });
  marksByMint.set(mint, arr);
}
for (const arr of marksByMint.values()) arr.sort((a, b) => a.at - b.at);

const byMintFirst = new Map();
for (const row of cacheMisses.sort((a, b) => a.selectedAt - b.selectedAt)) {
  if (!byMintFirst.has(row.mint)) byMintFirst.set(row.mint, row);
}

function recoveryFor(row, wait) {
  const marks = marksByMint.get(row.mint) || [];
  const target = row.selectedAt + wait;
  const mark = marks.find((m) => m.at >= target);
  if (!mark) return { wait, available: false };
  const valid = mark.mc <= maxEntryMcUsd;
  const outcome = valid ? score(mark.mc, mark.at, marks) : { outcome: "mc_rejected", pnlPct: null, exitMs: null };
  return {
    wait,
    available: true,
    mc: mark.mc,
    ageAfterTargetMs: mark.at - target,
    valid,
    ...outcome,
  };
}

const rows = [...byMintFirst.values()].map((row) => ({
  ...row,
  recoveries: waits.map((wait) => recoveryFor(row, wait)),
}));

const bucketRows = [];
for (const wait of waits) {
  const recs = rows.map((row) => row.recoveries.find((r) => r.wait === wait)).filter(Boolean);
  const valid = recs.filter((r) => r.valid);
  const pnls = valid.map((r) => r.pnlPct).filter(Number.isFinite);
  bucketRows.push({
    wait,
    uniqueMints: recs.length,
    stateAvailable: recs.filter((r) => r.available).length,
    stillValid: valid.length,
    tp: valid.filter((r) => r.outcome === "TP").length,
    sl: valid.filter((r) => r.outcome === "SL").length,
    none: valid.filter((r) => r.outcome === "none").length,
    net: sum(pnls),
    pf: pf(pnls),
  });
}

console.log("# Yellowstone / Cache-Miss Recovery Audit");
console.log("");
console.log("Read-only simulation. No live guard changes.");
console.log("");
console.log(`Since: ${new Date(sinceMs).toISOString()}`);
console.log(`Policy: ${policy}`);
console.log(`Selected rows: ${selected.length}`);
console.log(`Cache-miss reject rows: ${cacheMissRejects.length}`);
console.log(`Cache-miss unique mints: ${rows.length}`);
console.log("");
console.log("## Recovery Windows");
console.log("");
console.log(markdownTable(bucketRows, [
  { label: "Wait", value: (r) => `${r.wait}ms` },
  { label: "Mints", value: (r) => r.uniqueMints },
  { label: "State available", value: (r) => `${r.stateAvailable} (${fmtPct(pct(r.stateAvailable, r.uniqueMints))})` },
  { label: "Still MC-valid", value: (r) => `${r.stillValid} (${fmtPct(pct(r.stillValid, r.uniqueMints))})` },
  { label: "TP", value: (r) => r.tp },
  { label: "SL", value: (r) => r.sl },
  { label: "None", value: (r) => r.none },
  { label: "Net %", value: (r) => fmtPct(r.net) },
  { label: "PF", value: (r) => Number.isFinite(r.pf) ? fmt(r.pf, 2) : "inf" },
]));
console.log("");
console.log("## Top Recoverable TP Examples");
console.log("");
const examples = [];
for (const row of rows) {
  const firstTp = row.recoveries.find((r) => r.valid && r.outcome === "TP");
  if (firstTp) examples.push({ ...row, ...firstTp });
}
examples.sort((a, b) => a.wait - b.wait || (b.maxRoiPct || 0) - (a.maxRoiPct || 0));
console.log(markdownTable(examples.slice(0, 25), [
  { label: "Mint", value: (r) => `\`${r.mint}\`` },
  { label: "Wait", value: (r) => `${r.wait}ms` },
  { label: "Selected MC", value: (r) => r.selectedMc == null ? "n/a" : `$${fmt(r.selectedMc, 2)}` },
  { label: "Recovery MC", value: (r) => `$${fmt(r.mc, 2)}` },
  { label: "Outcome", value: (r) => r.outcome },
  { label: "Exit", value: (r) => r.exitMs == null ? "n/a" : `${fmt(r.exitMs, 0)}ms` },
  { label: "Max ROI", value: (r) => fmtPct(r.maxRoiPct) },
]));
console.log("");
console.log("## Verdict");
console.log("");
const best = bucketRows.slice().sort((a, b) => b.pf - a.pf || b.net - a.net)[0];
console.log(best
  ? `Best simulated wait bucket: ${best.wait}ms, valid=${best.stillValid}/${best.uniqueMints}, TP=${best.tp}, SL=${best.sl}, net=${fmtPct(best.net)}, PF=${Number.isFinite(best.pf) ? fmt(best.pf, 2) : "inf"}.`
  : "No recoverable cache-miss rows found.");
