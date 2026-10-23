#!/usr/bin/env node
import fs from "node:fs";

const auditFile = process.env.RUST_SELECTED_CANDIDATE_AUDIT_FILE
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-rule-a-economic-validation-20260902T033500Z/rust-selected-candidate-audit.jsonl";
const stdoutFile = process.env.RULE_A_ECONOMIC_STDOUT_FILE
  || auditFile.replace(/rust-selected-candidate-audit\.jsonl$/, "stdout.jsonl");
const checkpoint = Number(process.env.REPORT_CHECKPOINT || 0);

const rows = readJsonl(auditFile);
const stdoutRows = readJsonl(stdoutFile);
const banner = stdoutRows.find((row) => row.kind === "RULE_A_ECONOMIC_VALIDATION_MODE_ACTIVE");
const starts = rows.filter((row) => row.kind === "rustSelectedCandidateSniperStarted");
const start = starts.at(-1) || null;
const feeBanner = rows.find((row) => row.kind === "ruleADustFeeGuardVerified");

const byMint = groupBy(rows, (row) => row.mint || row.extra?.mint);
const live = [];
for (const [mint, group] of byMint.entries()) {
  const buy = first(group, (row) => row.kind === "rustSelectedCandidateBuyResult" && row.ok === true);
  if (!buy) continue;
  const finality = first(group, (row) => row.kind === "rustSelectedCandidateBuyFinality");
  const landed = finality && finality.status === "landed_ok" && !rowError(finality);
  const sell = first(group, (row) => row.kind === "rustExitSellBroadcast");
  const sellFinality = first(group, (row) => row.kind === "rustExitSellFinality");
  const sellAck = first(group, (row) => row.kind === "rustExitSellAck");
  const wouldSend = first(group, (row) => row.kind === "teacherImpulseProductionRuleACandidateQualified");
  const accepted = first(group, (row) => row.kind === "rustSelectedCandidateAccepted");
  const exactProof = first(group, (row) => row.kind === "rustSelectedCandidateExactBalanceProof" && row.ok === true);
  const hardStopRevalidations = group.filter((row) => row.kind === "rustExitHardStopRevalidation");
  const buyStart = first(group, (row) => row.kind === "rustSelectedCandidateBuyStart");
  const protectedMiss = first(group, (row) => row.kind === "rustSelectedCandidateProtectedMissedFill");
  live.push({
    mint,
    group,
    buy,
    buyStart,
    finality,
    landed,
    sell,
    sellFinality,
    sellAck,
    wouldSend,
    accepted,
    exactProof,
    protectedMiss,
    hardStopRevalidations,
    resolved: landed && sellFinality?.status === "landed_ok" && sellAck?.phase === "closed",
  });
}
live.sort((a, b) => atMs(a.buy) - atMs(b.buy));

const sample = checkpoint > 0 ? live.filter((row) => row.landed).slice(0, checkpoint) : live;
const resolved = sample.filter((row) => row.resolved);
const open = sample.filter((row) => row.landed && !row.resolved);
const pnl = resolved.map((row) => num(row.sell?.feeAdjustedPnlPct ?? row.sell?.pnlPct)).filter(Number.isFinite);
const wins = pnl.filter((value) => value > 0);
const losses = pnl.filter((value) => value < 0);
const pnlSolNorm = pnl.map((value) => value / 100);
const reasons = countBy(resolved, (row) => row.sell?.reason || "unknown");
const buyErrors = countBy(rows.filter((row) => row.kind === "rustSelectedCandidateBuyFinality" && rowError(row)), (row) => buyErrorBucket(rowError(row)));
const sellErrors = countBy(rows.filter((row) => row.kind === "rustExitSellFinality" && rowError(row)), (row) => sellErrorBucket(rowError(row)));
const rejects = countBy(rows.filter((row) => row.kind === "rustSelectedCandidateRejected" || row.kind === "teacherImpulseProductionRuleARejected"), (row) => row.reason || row.extra?.error || "unknown");
const hardStopConfirms = rows.filter((row) => row.kind === "rustExitHardStopRevalidation" && row.hardStopConfirmed === true).length;
const hardStopCancels = rows.filter((row) => row.kind === "rustExitHardStopRevalidation" && row.hardStopConfirmed === false).length;
const drainStates = rows.filter((row) => row.kind === "sessionDrainState");

const selectedToFirstSend = sample.map((row) => num(row.buy?.selectedToFirstSendMs)).filter(Number.isFinite);
const firstSendToAck = sample.map((row) => num(row.buy?.broadcastFirstAckMs ?? row.buy?.firstAckElapsedMs)).filter(Number.isFinite);
const selectedToBuyResult = sample.map((row) => num(row.buy?.selectedToBuyResultMs)).filter(Number.isFinite);
const ruleTruthToFirstSend = sample.map((row) => {
  const ruleAt = num(row.wouldSend?.ruleTruthAtMs ?? row.wouldSend?.candidateQualifiedAtMs);
  const sendAt = num(row.buy?.firstSendAtMs);
  return Number.isFinite(ruleAt) && Number.isFinite(sendAt) ? sendAt - ruleAt : null;
}).filter(Number.isFinite);
const exactProofMs = sample.map((row) => num(row.exactProof?.elapsedMs)).filter(Number.isFinite);
const entryMcDrift = sample.map((row) => {
  const shadowMc = num(row.wouldSend?.entryMarketCapUsd ?? row.accepted?.entryMarketCapUsd);
  const actualMc = num(row.exactProof?.impliedActualEntryMarketCapUsd ?? row.buy?.finalEconomicGuardEntryMarketCapUsd);
  return Number.isFinite(shadowMc) && shadowMc > 0 && Number.isFinite(actualMc)
    ? ((actualMc / shadowMc) - 1) * 100
    : null;
}).filter(Number.isFinite);
const tokenFillDegradation = sample.map((row) => num(row.exactProof?.actualFillDegradationPct)).filter(Number.isFinite);

print("Rule A Live Economic Cohort");
console.log(`Audit: ${auditFile}`);
console.log(`Stdout: ${stdoutFile}`);
console.log(`Banner: ${banner ? JSON.stringify(banner) : "missing"}`);
console.log(`Startup: ${start ? JSON.stringify(pick(start, ["buyLamports", "maxOpenPositions", "maxSessionAttempts", "maxLandedBuys", "buyOutputPolicy", "fillFirst", "sendTimeCurveCacheRequired", "submitEnabled"])) : "missing"}`);
console.log(`Fee guard: ${feeBanner ? JSON.stringify(feeBanner) : "missing_or_non_json"}`);

print("Cohort Status");
table([
  ["accepted", rows.filter((row) => row.kind === "rustSelectedCandidateAccepted").length],
  ["buy results", live.length],
  ["landed buys", sample.filter((row) => row.landed).length],
  ["resolved positions", resolved.length],
  ["open landed positions", open.length],
  ["latest landed count", rows.filter((row) => row.kind === "rustSelectedCandidateLandedBuyCounted").at(-1)?.landedBuyCount || 0],
  ["target landed", 50],
  ["session states", drainStates.map((row) => row.decision || row.state).join(", ") || "none"],
]);

print("Live Normalized Economics");
table([
  ["N resolved", resolved.length],
  ["WR", fmtPct(pct(wins.length, pnl.length))],
  ["PF", fmt(profitFactor(pnl))],
  ["expectancy", fmtPct(mean(pnl))],
  ["normalized PnL @ 1 SOL", fmtSol(sum(pnlSolNorm))],
  ["max drawdown", fmtPct(maxDrawdownPct(pnl))],
  ["longest loss streak", longestLossStreak(pnl)],
  ["TP", `${reasons.get("take-profit") || 0}`],
  ["hard-stop", `${reasons.get("hard-stop-loss") || 0}`],
  ["max-hold", `${reasons.get("max-hold") || 0}`],
]);

print("Entry And Buy Latency");
table([
  ["selected -> first send", stats(selectedToFirstSend)],
  ["first send -> ACK", stats(firstSendToAck)],
  ["selected -> buy result", stats(selectedToBuyResult)],
  ["RuleTruth -> firstSend", stats(ruleTruthToFirstSend)],
  ["entry MC degradation", stats(entryMcDrift, "%")],
  ["token fill degradation", stats(tokenFillDegradation, "%")],
]);

print("Inventory");
table([
  ["exact proof success", `${exactProofMs.length}/${sample.filter((row) => row.landed).length}`],
  ["exact proof latency", stats(exactProofMs)],
  ["last reconcile", JSON.stringify(rows.filter((row) => row.kind === "rustSelectedCandidateWalletReconcile").at(-1) || null)],
]);

print("Errors And Guards");
console.log("Buy errors:");
countTable(buyErrors);
console.log("Sell errors:");
countTable(sellErrors);
console.log("Intentional/local rejects:");
countTable(rejects);
table([
  ["hard-stop authoritative confirms", hardStopConfirms],
  ["hard-stop authoritative cancels", hardStopCancels],
  ["natural TP6003", rows.filter((row) => String(row.error || "").includes("6003")).length],
  ["6042", rows.filter((row) => String(row.error || row.finalityError || "").includes("6042")).length],
  ["3007", rows.filter((row) => String(row.error || row.finalityError || "").includes("3007")).length],
  ["real 6074", rows.filter((row) => String(row.error || row.finalityError || "").includes("6074")).length],
  ["6023", rows.filter((row) => String(row.error || row.finalityError || "").includes("6023")).length],
]);

print("Same-Mint Live Rows");
console.log(mdTable(sample.slice(-20).map((row) => ({
  mint: row.mint,
  shadowAt: atMs(row.wouldSend),
  buyMs: row.buy?.selectedToBuyResultMs,
  sendMs: row.buy?.selectedToFirstSendMs,
  expectedTokens: row.buy?.finalEconomicGuardExpectedTokensRaw,
  actualTokens: row.exactProof?.actualTokensOutRaw ?? row.buy?.tokensOutRaw,
  fillDeg: row.exactProof?.actualFillDegradationPct,
  exit: row.sell?.reason || (row.landed ? "open" : "not_landed"),
  pnl: row.sell?.feeAdjustedPnlPct,
  sellStatus: row.sellFinality?.status,
})), [
  ["Mint", "mint"],
  ["ShadowAt", (r) => r.shadowAt || "n/a"],
  ["BuyMs", "buyMs"],
  ["SendMs", "sendMs"],
  ["ExpectedTok", "expectedTokens"],
  ["ActualTok", "actualTokens"],
  ["FillDeg", (r) => fmtPct(r.fillDeg)],
  ["Exit", "exit"],
  ["PnL", (r) => fmtPct(r.pnl)],
  ["SellStatus", "sellStatus"],
]));

function readJsonl(file) {
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean).map((line) => {
    try { return JSON.parse(line); } catch { return null; }
  }).filter(Boolean);
}
function groupBy(items, keyFn) {
  const out = new Map();
  for (const item of items) {
    const key = keyFn(item);
    if (!key) continue;
    const group = out.get(key) || [];
    group.push(item);
    out.set(key, group);
  }
  for (const group of out.values()) group.sort((a, b) => atMs(a) - atMs(b));
  return out;
}
function first(items, predicate) {
  return items.find(predicate) || null;
}
function num(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}
function atMs(row) {
  return num(row?.atMs) ?? num(row?.candidateSelectedAtMs) ?? num(row?.candidateQualifiedAtMs) ?? num(row?.eventObservedAtMs) ?? 0;
}
function rowError(row) {
  return String(row?.error || row?.finalityError || row?.resultError || "");
}
function buyErrorBucket(error) {
  if (error.includes("6042")) return "6042";
  if (error.includes("2006")) return "2006";
  if (error.includes("3007")) return "3007";
  if (error.includes("6074")) return "6074";
  if (error.toLowerCase().includes("status")) return "status_timeout";
  if (error.toLowerCase().includes("authority")) return "authority_failure";
  if (error.toLowerCase().includes("sender")) return "sender_failure";
  return "other";
}
function sellErrorBucket(error) {
  if (error.includes("6003")) return "6003";
  if (error.includes("6023")) return "6023";
  if (error.includes("6074")) return "6074";
  if (error.toLowerCase().includes("status")) return "status_timeout";
  return "other";
}
function countBy(items, keyFn) {
  const out = new Map();
  for (const item of items) out.set(keyFn(item), (out.get(keyFn(item)) || 0) + 1);
  return out;
}
function sum(values) {
  return values.reduce((a, b) => a + b, 0);
}
function mean(values) {
  return values.length ? sum(values) / values.length : null;
}
function profitFactor(values) {
  const pos = sum(values.filter((v) => v > 0));
  const neg = Math.abs(sum(values.filter((v) => v < 0)));
  if (pos > 0 && neg === 0) return Infinity;
  return neg > 0 ? pos / neg : null;
}
function percentile(values, p) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  return sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * p) - 1)];
}
function maxDrawdownPct(values) {
  let equity = 0, peak = 0, dd = 0;
  for (const v of values) {
    equity += v;
    peak = Math.max(peak, equity);
    dd = Math.min(dd, equity - peak);
  }
  return dd;
}
function longestLossStreak(values) {
  let cur = 0, best = 0;
  for (const v of values) {
    if (v < 0) cur += 1;
    else cur = 0;
    best = Math.max(best, cur);
  }
  return best;
}
function pct(a, b) {
  return b ? (a / b) * 100 : null;
}
function fmt(value, digits = 3) {
  return Number.isFinite(value) ? value.toFixed(digits) : "n/a";
}
function fmtPct(value) {
  return Number.isFinite(value) ? `${value.toFixed(2)}%` : "n/a";
}
function fmtSol(value) {
  return Number.isFinite(value) ? `${value.toFixed(6)} SOL` : "n/a";
}
function stats(values, suffix = "ms") {
  const clean = values.filter(Number.isFinite);
  if (!clean.length) return "n/a";
  return `n=${clean.length} med=${fmt(percentile(clean, 0.50), 2)}${suffix} p75=${fmt(percentile(clean, 0.75), 2)}${suffix} p90=${fmt(percentile(clean, 0.90), 2)}${suffix} p95=${fmt(percentile(clean, 0.95), 2)}${suffix} worst=${fmt(Math.max(...clean), 2)}${suffix}`;
}
function print(title) {
  console.log(`\n## ${title}`);
}
function table(rows) {
  console.log("| Metric | Value |");
  console.log("| --- | --- |");
  for (const [k, v] of rows) console.log(`| ${k} | ${String(v).replaceAll("|", "/")} |`);
}
function countTable(map) {
  if (!map.size) {
    console.log("- none");
    return;
  }
  for (const [k, v] of [...map.entries()].sort((a, b) => b[1] - a[1])) console.log(`- ${k}: ${v}`);
}
function mdTable(items, columns) {
  if (!items.length) return "No rows.";
  const val = (row, col) => typeof col[1] === "function" ? col[1](row) : row[col[1]];
  return [
    `| ${columns.map((c) => c[0]).join(" | ")} |`,
    `| ${columns.map(() => "---").join(" | ")} |`,
    ...items.map((row) => `| ${columns.map((col) => String(val(row, col) ?? "").replaceAll("|", "/")).join(" | ")} |`),
  ].join("\n");
}
function pick(row, keys) {
  return Object.fromEntries(keys.map((key) => [key, row?.[key]]));
}
