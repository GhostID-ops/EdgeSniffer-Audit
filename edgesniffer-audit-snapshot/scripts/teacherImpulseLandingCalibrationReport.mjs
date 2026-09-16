#!/usr/bin/env node
import fs from "node:fs";

const selectedFile = process.env.TEACHER_IMPULSE_SELECTED_EVENT_OUTPUT
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-selected-events.jsonl";
const auditFile = process.env.RUST_SELECTED_CANDIDATE_AUDIT_FILE
  || "/var/lib/edgesniffer-teacher-impulse-parity-v1/live-mc5000-dust/rust-selected-candidate-audit.jsonl";
const minutes = clampInt(process.env.REPORT_WINDOW_MINUTES, 1, 24 * 60 * 14, 360);
const maxReadBytes = clampInt(process.env.REPORT_MAX_READ_BYTES, 1024 * 1024, 1024 * 1024 * 1024, 128 * 1024 * 1024);
const policyFilter = process.env.REPORT_POLICY || "";
const sinceMs = Date.now() - minutes * 60_000;

const selected = readJsonl(selectedFile)
  .filter((row) => rowTime(row) >= sinceMs)
  .filter((row) => !policyFilter || row.policy === policyFilter);
const selectedMints = new Set(selected.map((row) => row.mint).filter(Boolean));
const selectedKeys = new Set(selected.map((row) => row.key).filter(Boolean));
const audit = readJsonl(auditFile)
  .filter((row) => rowTime(row) >= sinceMs)
  .filter((row) => {
    if (!policyFilter) return true;
    if (row.key && selectedKeys.has(row.key)) return true;
    if (row.mint && selectedMints.has(row.mint)) return true;
    return false;
  });
const accepted = audit.filter((row) => row.kind === "rustSelectedCandidateAccepted");
const buyResults = audit.filter((row) => row.kind === "rustSelectedCandidateBuyResult");
const buyFinality = audit.filter((row) => row.kind === "rustSelectedCandidateBuyFinality");
const latestBuyInstructionAuditByKey = latestByKey(
  audit.filter((row) =>
    row.kind === "rustSelectedCandidateBuyStart"
    || row.kind === "rustSelectedCandidateBuyResult"
  )
);
const counterfactual6042 = audit.filter((row) =>
  row.kind === "rustSelectedCandidate6042Counterfactual"
  || row.kind === "rustSelectedCandidateBuy6042Counterfactual"
  || String(row.error || row.finalityError || row.resultError || "").includes("6042")
);
const rejected = audit.filter((row) => row.kind === "rustSelectedCandidateRejected");
const skipped = audit.filter((row) => row.kind === "rustSelectedCandidateSkipped");

const landedFinality = buyFinality.filter((row) => !isFailure(row) && !isUnknownFinality(row));
const unknownFinality = buyFinality.filter(isUnknownFinality);
const failedFinality = buyFinality.filter((row) => isFailure(row) && !isUnknownFinality(row));
const failed6042 = failedFinality.filter((row) => rowError(row).includes("6042"));
const failedOther = failedFinality.filter((row) => !rowError(row).includes("6042"));
const protectedMisses = [...rejected, ...skipped].filter((row) =>
  [
    row.reason,
    row.rejectReason,
    row.skipReason,
    row.entryGateReason,
    row.driftGateReason,
  ].some((value) => String(value || "").toLowerCase().includes("drift")
    || String(value || "").toLowerCase().includes("above_max")
    || String(value || "").toLowerCase().includes("min_out")
    || String(value || "").toLowerCase().includes("protected"))
);

const wouldPass = counterfactual6042.filter((row) =>
  row.wouldPassSubmittedMinAtCounterfactualQuote === true
  || row.wouldPassRelaxedMinOut === true
  || row.currentQuoteWouldPassSubmittedMin === true
);
const wouldNotPass = counterfactual6042.filter((row) =>
  row.wouldPassSubmittedMinAtCounterfactualQuote === false
  || row.wouldPassRelaxedMinOut === false
  || row.currentQuoteWouldPassSubmittedMin === false
);
const insideMc = counterfactual6042.filter((row) =>
  row.insideMaxEntryMarketCap === true
  || row.insideMaxEntryMarketCapAfterDrift === true
  || row.insideDriftBand === true
);
const failed6042ByLayout = group6042ByLayout(failed6042, latestBuyInstructionAuditByKey);

const selectedToBuyResultMs = numericValues(buyResults, [
  "selectedToBuyResultMs",
  "selectedToBuyMs",
  "elapsedMs",
]);
const buyBroadcastMs = numericValues(buyResults, [
  "buyBroadcastMs",
  "selectedToBuyBroadcastMs",
  "broadcastMs",
]);
const finalityMs = numericValues(buyFinality, [
  "buyFinalityMs",
  "selectedToFinalityMs",
  "selectedToBuyFinalityMs",
  "elapsedMs",
]);
const buyResultToFinalityMs = numericValues(buyFinality, [
  "buyResultToFinalityMs",
]);
const exactBalanceProof = audit.filter((row) => row.kind === "rustSelectedCandidateExactBalanceProof");
const exactBalanceProofMs = numericValues(exactBalanceProof, [
  "elapsedMs",
  "buyResultToExactBalanceProofMs",
  "selectedToExactBalanceProofMs",
]);
const earlyBalanceProof = audit.filter((row) =>
  row.kind === "rustSelectedCandidateEarlyBalanceUpdate"
  || row.kind === "rustSelectedCandidateEarlyBalanceCacheHit"
  || row.kind === "rustSelectedCandidateStreamingExactBalanceActivation"
);
const earlyBalanceProofMs = numericValues(earlyBalanceProof, [
  "elapsedMs",
  "buyResultToEarlyBalanceProofMs",
  "selectedToEarlyBalanceProofMs",
]);
const exactBalanceCacheUpdates = audit.filter((row) =>
  row.kind === "rustSelectedCandidateBalanceCacheUpdate"
  || row.kind === "rustSelectedCandidateBalanceCacheSubmittedBuyExactProofUpdate"
);
const exitMarks = audit.filter((row) => row.kind === "rustExitMark");
const executableOutcomes = buyResults.map((buy) => executableOutcomeForBuy(buy, exitMarks));
const executableHitTp = executableOutcomes.filter((row) => row.hitTp20).length;
const executableHitSlFirst = executableOutcomes.filter((row) => row.firstThreshold === "SL").length;
const executableUnknown = executableOutcomes.filter((row) => row.markCount === 0).length;

const byMint = new Map();
for (const row of audit) {
  const mint = row.mint;
  if (!mint) continue;
  const state = byMint.get(mint) || {
    mint,
    selected: selectedMints.has(mint),
    accepted: false,
    buyResults: 0,
    landed: false,
    failed6042: 0,
    protectedMiss: 0,
    lastReason: "",
    lastAtMs: 0,
  };
  state.accepted ||= row.kind === "rustSelectedCandidateAccepted";
  state.buyResults += row.kind === "rustSelectedCandidateBuyResult" ? 1 : 0;
  if (row.kind === "rustSelectedCandidateBuyFinality" && !isFailure(row) && !isUnknownFinality(row)) state.landed = true;
  if (row.kind === "rustSelectedCandidateBuyFinality" && rowError(row).includes("6042")) state.failed6042++;
  if (protectedMisses.includes(row)) state.protectedMiss++;
  const atMs = rowTime(row);
  if (atMs >= state.lastAtMs) {
    state.lastAtMs = atMs;
    state.lastReason = row.reason || row.rejectReason || row.skipReason || row.error || row.finalityError || row.status || row.kind || "";
  }
  byMint.set(mint, state);
}

const rows = [...byMint.values()].sort((a, b) => b.lastAtMs - a.lastAtMs).slice(0, 12);

printSection("Teacher Impulse Landing Calibration");
console.log(`Window: ${minutes} minutes`);
console.log(`Policy filter: ${policyFilter || "none"}`);
console.log(`Selected candidates: ${selected.length} rows / ${selectedMints.size} unique mints`);
console.log(`Rust accepted candidates: ${accepted.length} rows / ${new Set(accepted.map((row) => row.mint).filter(Boolean)).size} unique mints`);
console.log(`Buy result rows: ${buyResults.length}`);
console.log(`Landed buys: ${landedFinality.length}`);
console.log(`Unknown/status-timeout buys: ${unknownFinality.length}`);
console.log(`Failed finality: ${failedFinality.length} (${failed6042.length} Custom 6042, ${failedOther.length} other)`);
console.log(`Protected/drift/min-out skips: ${protectedMisses.length}`);

printSection("6042 Counterfactuals");
console.log(`6042/counterfactual rows: ${counterfactual6042.length}`);
console.log(`Would pass submitted/relaxed min-out: ${wouldPass.length}`);
console.log(`Would not pass submitted/relaxed min-out: ${wouldNotPass.length}`);
console.log(`Inside MC/drift band at counterfactual quote: ${insideMc.length}`);
console.log("6042 by submitted layout:");
for (const row of failed6042ByLayout) {
  console.log(`- ${row.layout}: ${row.count}`);
}

printSection("Executable After Worse Fill");
console.log(`Buy attempts with exit marks: ${executableOutcomes.length - executableUnknown}/${executableOutcomes.length}`);
console.log(`Would hit TP20 after submitted/calibrated fill: ${executableHitTp}`);
console.log(`Would hit SL20 before TP20: ${executableHitSlFirst}`);
for (const row of executableOutcomes.slice(-8)) {
  console.log([
    row.mint,
    `marks=${row.markCount}`,
    `peak=${formatPct(row.peakPnlPct)}`,
    `last=${formatPct(row.lastPnlPct)}`,
    `first=${row.firstThreshold || "none"}`,
  ].join(" | "));
}

printSection("Speed");
console.log(`selected -> buy result ms: ${formatStats(selectedToBuyResultMs)}`);
console.log(`selected -> broadcast ms: ${formatStats(buyBroadcastMs)}`);
console.log(`selected -> finality ms: ${formatStats(finalityMs)}`);
console.log(`buy result -> finality ms: ${formatStats(buyResultToFinalityMs)}`);
console.log(`exact balance proof elapsed ms: ${formatStats(exactBalanceProofMs)}`);
console.log(`early/local balance proof elapsed ms: ${formatStats(earlyBalanceProofMs)}`);
console.log(`exact balance/cache proof rows: ${exactBalanceProof.length} direct / ${exactBalanceCacheUpdates.length} stream / ${earlyBalanceProof.length} early-activation`);

printSection("Recent Mint Outcomes");
for (const row of rows) {
  console.log([
    row.mint,
    `selected=${row.selected}`,
    `accepted=${row.accepted}`,
    `buyResults=${row.buyResults}`,
    `landed=${row.landed}`,
    `6042=${row.failed6042}`,
    `protected=${row.protectedMiss}`,
    `last=${row.lastReason}`,
  ].join(" | "));
}

function readJsonl(file) {
  if (!fs.existsSync(file)) return [];
  const stat = fs.statSync(file);
  const start = Math.max(0, stat.size - maxReadBytes);
  const fd = fs.openSync(file, "r");
  let text = "";
  try {
    const buf = Buffer.alloc(stat.size - start);
    fs.readSync(fd, buf, 0, buf.length, start);
    text = buf.toString("utf8");
  } finally {
    fs.closeSync(fd);
  }
  if (start > 0) {
    const firstNewline = text.indexOf("\n");
    text = firstNewline >= 0 ? text.slice(firstNewline + 1) : "";
  }
  return text
    .split(/\n/)
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

function rowTime(row) {
  if (Number.isFinite(row.atMs)) return row.atMs;
  if (Number.isFinite(row.observedAtMs)) return row.observedAtMs;
  if (Number.isFinite(row.entryAtMs)) return row.entryAtMs;
  if (row.at) {
    const parsed = Date.parse(row.at);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function isFailure(row) {
  const status = String(row.status || row.finalityStatus || "").toLowerCase();
  const err = rowError(row);
  return status.includes("error")
    || status.includes("fail")
    || err.length > 0
    || row.ok === false
    || row.success === false;
}

function isUnknownFinality(row) {
  const status = String(row.status || row.finalityStatus || "").toLowerCase();
  return status.includes("timeout")
    || status.includes("unknown")
    || status.includes("pending");
}

function rowError(row) {
  return [
    row.error,
    row.finalityError,
    row.resultError,
    row.err,
    row.transactionError,
  ].filter(Boolean).map(String).join(" ");
}

function latestByKey(rows) {
  const map = new Map();
  for (const row of rows) {
    if (!row.key) continue;
    const previous = map.get(row.key);
    if (!previous || rowTime(row) >= rowTime(previous)) map.set(row.key, row);
  }
  return map;
}

function group6042ByLayout(rows, auditByKey) {
  const counts = new Map();
  for (const row of rows) {
    const auditRow = row.key ? auditByKey.get(row.key) : null;
    const audit = auditRow && extractExecutorBuyInstructionAudit(auditRow);
    const fallback = auditRow?.buyInstructionAudit || {};
    const layout = [
      audit?.accountLayout || "layout_unknown",
      audit?.discriminatorKind || fallback.sourceIxDataDiscriminatorKind || "discriminator_unknown",
      audit?.tokenProgramId || fallback.tokenProgramId || "token_program_unknown",
    ].join(" / ");
    counts.set(layout, (counts.get(layout) || 0) + 1);
  }
  return [...counts.entries()]
    .map(([layout, count]) => ({ layout, count }))
    .sort((a, b) => b.count - a.count || a.layout.localeCompare(b.layout));
}

function extractExecutorBuyInstructionAudit(row) {
  const wrapped = row.executorBuyInstructionAudit;
  if (!wrapped) return null;
  if (wrapped.ok === true && wrapped.audit) return wrapped.audit;
  if (wrapped.accountLayout || wrapped.discriminatorKind) return wrapped;
  return null;
}

function numericValues(rows, keys) {
  const out = [];
  for (const row of rows) {
    for (const key of keys) {
      const value = Number(row[key]);
      if (Number.isFinite(value)) {
        out.push(value);
        break;
      }
    }
  }
  return out;
}

function executableOutcomeForBuy(buy, marks) {
  const buyAtMs = rowTime(buy);
  const scoped = marks
    .filter((mark) => mark.mint === buy.mint)
    .filter((mark) => rowTime(mark) >= buyAtMs && rowTime(mark) <= buyAtMs + 120_000)
    .sort((a, b) => rowTime(a) - rowTime(b));
  let firstThreshold = "";
  let peakPnlPct = null;
  let lastPnlPct = null;
  for (const mark of scoped) {
    const pnl = Number(mark.pnlPct);
    if (!Number.isFinite(pnl)) continue;
    peakPnlPct = peakPnlPct == null ? pnl : Math.max(peakPnlPct, pnl);
    lastPnlPct = pnl;
    if (!firstThreshold && pnl >= 20) firstThreshold = "TP";
    if (!firstThreshold && pnl <= -20) firstThreshold = "SL";
  }
  return {
    mint: buy.mint,
    markCount: scoped.length,
    peakPnlPct,
    lastPnlPct,
    hitTp20: (peakPnlPct ?? -Infinity) >= 20,
    firstThreshold,
  };
}

function formatPct(value) {
  return Number.isFinite(value) ? `${value.toFixed(2)}%` : "n/a";
}

function formatStats(values) {
  if (values.length === 0) return "n/a";
  const sorted = [...values].sort((a, b) => a - b);
  const avg = sorted.reduce((sum, value) => sum + value, 0) / sorted.length;
  return [
    `n=${sorted.length}`,
    `avg=${avg.toFixed(1)}`,
    `p50=${percentile(sorted, 0.5).toFixed(1)}`,
    `p90=${percentile(sorted, 0.9).toFixed(1)}`,
    `min=${sorted[0].toFixed(1)}`,
    `max=${sorted.at(-1).toFixed(1)}`,
  ].join(" ");
}

function percentile(sorted, p) {
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * p)));
  return sorted[idx];
}

function printSection(title) {
  console.log(`\n${title}`);
  console.log("-".repeat(title.length));
}

function clampInt(value, min, max, fallback) {
  const parsed = Number.parseInt(value ?? "", 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}
