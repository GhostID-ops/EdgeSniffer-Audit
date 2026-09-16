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
const policy = process.env.REPORT_POLICY
  || "teacher_impulse_mc5000_tp20_sl20_landing_calibration_v2";
const sinceMs = process.env.REPORT_SINCE_MS
  ? Number(process.env.REPORT_SINCE_MS)
  : Date.parse(process.env.REPORT_SINCE || "2026-08-29T00:50:02Z");
const horizonMs = Number(process.env.REPORT_HORIZON_MS || 60_000);
const tpPct = Number(process.env.REPORT_TP_PCT || 20);
const slPct = Number(process.env.REPORT_SL_PCT || -20);
const buySol = Number(process.env.REPORT_BUY_SOL || 0.001);
const computeUnitLimit = Number(process.env.REPORT_COMPUTE_UNIT_LIMIT || 400_000);
const maxTailBytes = Number(process.env.REPORT_MAX_TAIL_BYTES || 256 * 1024 * 1024);

function readTailJsonl(file, maxBytes = maxTailBytes) {
  if (!fs.existsSync(file)) return [];
  const stat = fs.statSync(file);
  const start = Math.max(0, stat.size - maxBytes);
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
    const idx = text.indexOf("\n");
    text = idx >= 0 ? text.slice(idx + 1) : "";
  }
  return text
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

function num(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function atMs(row) {
  return num(row?.atMs)
    ?? num(row?.entryAtMs)
    ?? num(row?.decidedAtMs)
    ?? num(row?.observedAtMs)
    ?? Date.parse(row?.at || "");
}

function rowMint(row) {
  return row?.mint || row?.extra?.mint || row?.candidate?.mint || null;
}

function round(value, digits = 3) {
  return Number.isFinite(value) ? Number(value.toFixed(digits)) : null;
}

function pct(count, total) {
  return total > 0 ? round((count / total) * 100, 2) : 0;
}

function markdownTable(rows, columns) {
  if (!rows.length) return "No rows.";
  return [
    `| ${columns.map((c) => c.label).join(" | ")} |`,
    `| ${columns.map(() => "---").join(" | ")} |`,
    ...rows.map((row) =>
      `| ${columns.map((c) => String(c.value(row) ?? "").replaceAll("|", "/")).join(" | ")} |`
    ),
  ].join("\n");
}

function formatPct(value) {
  return Number.isFinite(value) ? `${round(value, 2)}%` : "n/a";
}

function estimatePriorityLamports(priorityFeeMicroLamports, units = computeUnitLimit) {
  const priority = BigInt(Math.max(0, num(priorityFeeMicroLamports, 0)));
  const cappedUnits = BigInt(Math.max(1, Math.min(1_400_000, num(units, computeUnitLimit))));
  return Number((priority * cappedUnits + 999_999n) / 1_000_000n);
}

function estimateExecutionFeeLamports(priorityFeeMicroLamports, jitoTipLamports, heliusTipLamports, units = computeUnitLimit) {
  return estimatePriorityLamports(priorityFeeMicroLamports, units)
    + Math.max(0, num(jitoTipLamports, 0))
    + Math.max(0, num(heliusTipLamports, 0));
}

function firstByMint(rows, predicate = () => true) {
  const out = new Map();
  for (const row of rows) {
    const mint = rowMint(row);
    if (!mint || !predicate(row)) continue;
    const old = out.get(mint);
    if (!old || atMs(row) < atMs(old)) out.set(mint, row);
  }
  return out;
}

function groupByMint(rows, predicate = () => true) {
  const out = new Map();
  for (const row of rows) {
    const mint = rowMint(row);
    if (!mint || !predicate(row)) continue;
    const group = out.get(mint) || [];
    group.push(row);
    out.set(mint, group);
  }
  for (const group of out.values()) {
    group.sort((a, b) => atMs(a) - atMs(b));
  }
  return out;
}

function firstInGroup(groups, mint, predicate = () => true) {
  return (groups.get(mint) || []).find(predicate) || null;
}

function shadowFirstDirection(shadow) {
  if (!shadow) return "missing";
  if (shadow.firstTpMs != null && (shadow.firstSlMs == null || shadow.firstTpMs <= shadow.firstSlMs)) {
    return "TP";
  }
  if (shadow.firstSlMs != null) return "SL";
  return "none";
}

function shadowFirstMs(shadow) {
  if (!shadow) return null;
  return shadowFirstDirection(shadow) === "TP"
    ? shadow.firstTpMs
    : shadow.firstSlMs ?? null;
}

function buyErrorBucket(error) {
  const text = String(error || "");
  if (text.includes("6042")) return "buy_6042";
  if (text.includes("3007")) return "buy_3007";
  if (text) return "buy_other_error";
  return null;
}

const selected = readTailJsonl(selectedFile)
  .filter((row) => atMs(row) >= sinceMs)
  .filter((row) => !policy || row.policy === policy);
const audit = readTailJsonl(auditFile)
  .filter((row) => atMs(row) >= sinceMs);

const selectedByMint = firstByMint(selected);
const buyResultByMint = firstByMint(audit, (row) => row.kind === "rustSelectedCandidateBuyResult");
const buyFinalityByMint = firstByMint(audit, (row) => row.kind === "rustSelectedCandidateBuyFinality");
const exitDecisionByMint = firstByMint(audit, (row) => row.kind === "rustExitDecision");
const curveHitByMint = firstByMint(audit, (row) => row.kind === "rustSelectedCandidateCurveCacheHit");
const rejectedByMint = firstByMint(audit, (row) => row.kind === "rustSelectedCandidateRejected");
const protectedMissedByMint = firstByMint(audit, (row) => row.kind === "rustSelectedCandidateProtectedMissedFill");
const counterfactual6042ByMint = firstByMint(audit, (row) => row.kind === "rustSelectedCandidate6042Counterfactual");
const sellFinalityGroups = groupByMint(audit, (row) => row.kind === "rustExitSellFinality");

const selectedMints = new Set([...selectedByMint.keys()]);
const shadowEntries = new Map();
for (const mint of selectedMints) {
  const selectedRow = selectedByMint.get(mint);
  if (!selectedRow) continue;
  shadowEntries.set(mint, {
    mint,
    entryAtMs: atMs(selectedRow),
    entryMarketCapUsd: num(selectedRow.entryMarketCapUsd),
    firstTpMs: null,
    firstSlMs: null,
    maxRoiPct: null,
    minRoiPct: null,
    marks: 0,
  });
}

await scanCapsulesForShadowMarks(capsulesFile, shadowEntries);

const rows = [...selectedMints].map((mint) => {
  const selectedRow = selectedByMint.get(mint);
  const buy = buyResultByMint.get(mint);
  const finality = buyFinalityByMint.get(mint);
  const exit = exitDecisionByMint.get(mint);
  const firstSell = firstInGroup(sellFinalityGroups, mint);
  const landedSell = firstInGroup(sellFinalityGroups, mint, (row) => row.status === "landed_ok" && !row.error);
  const curve = curveHitByMint.get(mint);
  const rejected = rejectedByMint.get(mint);
  const protectedMissed = protectedMissedByMint.get(mint);
  const counterfactual6042 = counterfactual6042ByMint.get(mint);
  const shadow = shadowEntries.get(mint);
  const buyFeeLamports = estimateExecutionFeeLamports(
    buy?.priorityFeeMicroLamports,
    buy?.jitoTipLamports,
    buy?.heliusTipLamports,
  );
  const sellFeeLamports = num(exit?.decisionExecutionFeeLamports)
    ?? estimateExecutionFeeLamports(
      exit?.priorityFeeMicroLamports,
      exit?.jitoTipLamports,
      exit?.heliusTipLamports,
    );
  const buyTipsLamports = num(buy?.jitoTipLamports, 0) + num(buy?.heliusTipLamports, 0);
  const livePnlPct = num(exit?.feeAdjustedPnlPct ?? exit?.pnlPct);
  const buyBucket = buyErrorBucket(finality?.error);
  let disposition = "selected_no_buy_result";
  if (buyBucket) {
    disposition = buyBucket;
  } else if (buy && finality?.status === "landed_ok" && landedSell && Number.isFinite(livePnlPct)) {
    disposition = livePnlPct > 0 ? "live_closed_win" : "live_closed_loss";
  } else if (buy && finality?.status === "landed_ok" && firstSell && !landedSell) {
    disposition = "sell_finality_failed";
  } else if (buy && finality?.status === "landed_ok") {
    disposition = "live_landed_open_or_unclosed";
  } else if (buy && !finality) {
    disposition = "buy_result_no_finality_yet";
  } else if (!buy && rejected) {
    disposition = `rust_rejected:${rejected.reason || "unknown"}`;
  }
  const shadowFirst = shadowFirstDirection(shadow);
  return {
    mint,
    selectedAtMs: atMs(selectedRow),
    shadowEntryMc: num(selectedRow?.entryMarketCapUsd),
    buyMs: num(buy?.selectedToBuyResultMs),
    broadcastMs: num(buy?.broadcastMs),
    finalityMs: num(finality?.selectedToFinalityMs),
    buyStatus: finality?.status || null,
    buyError: finality?.error || null,
    protectedMissedReason: protectedMissed?.reason || null,
    rejectedReason: buy ? null : rejected?.reason || null,
    disposition,
    curveSource: curve?.cacheSource || null,
    independentCurve: curve?.cacheAccepted === true,
    sourceIxAccountCount: num(buy?.executorBuyInstructionAudit?.audit?.sourceIxAccountCount)
      ?? num(buy?.buyInstructionAudit?.sourceIxAccountCount),
    accountLayout: buy?.executorBuyInstructionAudit?.audit?.accountLayout || null,
    discriminatorKind: buy?.executorBuyInstructionAudit?.audit?.discriminatorKind
      || buy?.buyInstructionAudit?.sourceIxDataDiscriminatorKind
      || null,
    reserveSource: selectedRow?.reserveSource || buy?.reserveSource || null,
    currentVsQuotedDriftBps: num(counterfactual6042?.currentVsQuotedDriftBps),
    submittedMinVsCurrentQuotePct: num(counterfactual6042?.submittedMinVsCurrentQuotePct),
    wouldPassSubmittedMinAtCounterfactualQuote: counterfactual6042?.wouldPassSubmittedMinAtCounterfactualQuote,
    livePnlPct,
    liveApproxPnlSol: Number.isFinite(livePnlPct) ? (livePnlPct / 100) * buySol : null,
    exitReason: exit?.reason || null,
    sellStatus: landedSell?.status || firstSell?.status || null,
    sellError: landedSell?.error || firstSell?.error || null,
    sellAttempts: (sellFinalityGroups.get(mint) || []).length,
    buyTipsSol: buyTipsLamports / 1e9,
    buyFeeSol: buyFeeLamports / 1e9,
    sellFeeSol: sellFeeLamports / 1e9,
    totalFeeSol: (buyFeeLamports + sellFeeLamports) / 1e9,
    feeToBuyPct: buySol > 0 ? ((buyFeeLamports + sellFeeLamports) / 1e9 / buySol) * 100 : null,
    shadowMarks: shadow?.marks ?? 0,
    shadowFirst,
    shadowFirstMs: shadowFirstMs(shadow),
    shadowMaxRoiPct: shadow?.maxRoiPct ?? null,
    shadowMinRoiPct: shadow?.minRoiPct ?? null,
  };
});

const attempted = rows.filter((row) => buyResultByMint.has(row.mint));
const landed = rows.filter((row) => row.buyStatus === "landed_ok" && !row.buyError);
const closed = landed.filter((row) => row.sellStatus === "landed_ok" && Number.isFinite(row.livePnlPct));
const failed6042 = rows.filter((row) => String(row.buyError || "").includes("6042"));
const failed3007 = rows.filter((row) => String(row.buyError || "").includes("3007"));
const failedOther = rows.filter((row) => row.buyError && !String(row.buyError).includes("6042") && !String(row.buyError).includes("3007"));
const sellFailed = landed.filter((row) => row.sellStatus && row.sellStatus !== "landed_ok");
const selectedShadowTp = rows.filter((row) => row.shadowFirst === "TP");
const selectedShadowSl = rows.filter((row) => row.shadowFirst === "SL");
const selectedShadowNone = rows.filter((row) => row.shadowFirst === "none");
const attemptedShadowTp = attempted.filter((row) => row.shadowFirst === "TP");
const landedShadowTp = landed.filter((row) => row.shadowFirst === "TP");
const missedShadowTp = selectedShadowTp.filter((row) => row.disposition !== "live_closed_win");
const sameMintDirection = closed.filter((row) => row.shadowFirst !== "none" && row.shadowFirst !== "missing");
const matching = sameMintDirection.filter((row) =>
  (row.livePnlPct > 0 && row.shadowFirst === "TP")
  || (row.livePnlPct <= 0 && row.shadowFirst === "SL")
);
const liveWins = closed.filter((row) => row.livePnlPct > 0).length;
const configuredTipSol = rows.reduce((sum, row) => sum + row.buyTipsSol, 0);
const configuredExecutionFeeSol = rows.reduce((sum, row) => sum + row.totalFeeSol, 0);
const feeRows = attempted.filter((row) => row.feeToBuyPct != null);
const avgFeeToBuyPct = feeRows.length > 0
  ? feeRows.reduce((sum, row) => sum + (row.feeToBuyPct || 0), 0) / feeRows.length
  : 0;
const buyMsRows = rows.map((row) => row.buyMs).filter(Number.isFinite).sort((a, b) => a - b);
const medianBuyMs = buyMsRows.length ? buyMsRows[Math.floor(buyMsRows.length / 2)] : null;
const dispositionCounts = new Map();
const rejectCounts = new Map();
for (const row of rows) {
  dispositionCounts.set(row.disposition, (dispositionCounts.get(row.disposition) || 0) + 1);
  if (!buyResultByMint.has(row.mint) && row.rejectedReason) {
    rejectCounts.set(row.rejectedReason, (rejectCounts.get(row.rejectedReason) || 0) + 1);
  }
}
const countRows = (counts) => [...counts.entries()]
  .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  .map(([name, count]) => ({ name, count }));

console.log("# Teacher Impulse Current-Policy Live/Shadow Parity");
console.log("");
console.log(`Policy: ${policy}`);
console.log(`Since: ${new Date(sinceMs).toISOString()}`);
console.log(`Shadow rule: TP ${tpPct}% / SL ${slPct}% / horizon ${horizonMs}ms`);
console.log("");
console.log("| Metric | Value |");
console.log("|---|---:|");
console.log(`| Selected rows | ${selected.length} |`);
console.log(`| Selected mints | ${new Set(selected.map((row) => row.mint).filter(Boolean)).size} |`);
console.log(`| Live buy attempts | ${attempted.length} |`);
console.log(`| Landed buys | ${landed.length} |`);
console.log(`| Closed landed trades | ${closed.length} |`);
console.log(`| Live wins | ${liveWins} |`);
console.log(`| Live win rate | ${formatPct(pct(liveWins, closed.length))} |`);
console.log(`| Live approx PnL at ${buySol} SOL | ${round(closed.reduce((s, r) => s + (r.liveApproxPnlSol || 0), 0), 9)} SOL |`);
console.log(`| Failed 6042 | ${failed6042.length} |`);
console.log(`| Failed 3007 | ${failed3007.length} |`);
console.log(`| Failed other | ${failedOther.length} |`);
console.log(`| Failed sell finality after landed buy | ${sellFailed.length} |`);
console.log(`| Selected shadow TP mints | ${selectedShadowTp.length} |`);
console.log(`| Selected shadow SL mints | ${selectedShadowSl.length} |`);
console.log(`| Selected shadow neither mints | ${selectedShadowNone.length} |`);
console.log(`| Attempted shadow TP mints | ${attemptedShadowTp.length} |`);
console.log(`| Landed shadow TP mints | ${landedShadowTp.length} |`);
console.log(`| Missed/unrealized shadow TP mints | ${missedShadowTp.length} |`);
console.log(`| Same-mint shadow direction rows | ${sameMintDirection.length} |`);
console.log(`| Live/shadow direction matches | ${matching.length}/${sameMintDirection.length} (${formatPct(pct(matching.length, sameMintDirection.length))}) |`);
console.log(`| Median selected-to-buy-result | ${medianBuyMs ?? "n/a"} ms |`);
console.log(`| Approx configured buy tip exposure in attempts | ${round(configuredTipSol, 6)} SOL |`);
console.log(`| Approx configured buy/sell execution-fee exposure | ${round(configuredExecutionFeeSol, 6)} SOL |`);
console.log(`| Avg configured fee exposure vs ${buySol} SOL dust buy | ${formatPct(avgFeeToBuyPct)} |`);
console.log("");
console.log("## Execution Buckets");
console.log(markdownTable(countRows(dispositionCounts), [
  { label: "Bucket", value: (r) => r.name },
  { label: "Mints", value: (r) => r.count },
]));
console.log("");
console.log("## Rust Reject Reasons");
console.log(markdownTable(countRows(rejectCounts), [
  { label: "Reason", value: (r) => r.name },
  { label: "Mints", value: (r) => r.count },
]));
console.log("");
console.log("## Missed Or Unrealized Shadow TP Mints");
console.log(markdownTable(missedShadowTp, [
  { label: "Mint", value: (r) => `\`${r.mint}\`` },
  { label: "Disposition", value: (r) => r.disposition },
  { label: "Shadow TP ms", value: (r) => r.shadowFirstMs ?? "n/a" },
  { label: "Shadow max", value: (r) => formatPct(r.shadowMaxRoiPct) },
  { label: "Buy ms", value: (r) => r.buyMs ?? "n/a" },
  { label: "Buy status", value: (r) => r.buyStatus || "n/a" },
  { label: "Reject", value: (r) => r.rejectedReason || "" },
  { label: "Error", value: (r) => r.buyError ? String(r.buyError).slice(0, 45) : (r.sellError ? String(r.sellError).slice(0, 45) : "") },
]));
console.log("");
console.log("## Failed Execution Details");
console.log(markdownTable(rows.filter((row) => row.disposition.startsWith("buy_") || row.disposition === "sell_finality_failed"), [
  { label: "Mint", value: (r) => `\`${r.mint}\`` },
  { label: "Bucket", value: (r) => r.disposition },
  { label: "Shadow", value: (r) => r.shadowFirst },
  { label: "Shadow ms", value: (r) => r.shadowFirstMs ?? "n/a" },
  { label: "Buy ms", value: (r) => r.buyMs ?? "n/a" },
  { label: "Finality ms", value: (r) => r.finalityMs ?? "n/a" },
  { label: "Layout", value: (r) => r.accountLayout || "n/a" },
  { label: "Disc", value: (r) => r.discriminatorKind || "n/a" },
  { label: "Ix accounts", value: (r) => r.sourceIxAccountCount ?? "n/a" },
  { label: "Reserve", value: (r) => r.reserveSource || "n/a" },
  { label: "6042 drift bps", value: (r) => r.currentVsQuotedDriftBps ?? "" },
  { label: "6042 min/current", value: (r) => formatPct(r.submittedMinVsCurrentQuotePct) },
  { label: "6042 would pass now", value: (r) => r.wouldPassSubmittedMinAtCounterfactualQuote ?? "" },
  { label: "Error", value: (r) => r.buyError ? String(r.buyError).slice(0, 45) : (r.sellError ? String(r.sellError).slice(0, 45) : "") },
]));
console.log("");
console.log("## Same-Mint Rows");
console.log(markdownTable(rows, [
  { label: "Mint", value: (r) => `\`${r.mint}\`` },
  { label: "Disposition", value: (r) => r.disposition },
  { label: "Buy ms", value: (r) => r.buyMs ?? "n/a" },
  { label: "Finality ms", value: (r) => r.finalityMs ?? "n/a" },
  { label: "Buy status", value: (r) => r.buyStatus || "n/a" },
  { label: "Live PnL", value: (r) => formatPct(r.livePnlPct) },
  { label: "Live exit", value: (r) => r.exitReason || "n/a" },
  { label: "Sell", value: (r) => r.sellStatus || "n/a" },
  { label: "Sell attempts", value: (r) => r.sellAttempts || "" },
  { label: "Shadow first", value: (r) => r.shadowFirst },
  { label: "Shadow first ms", value: (r) => r.shadowFirstMs ?? "n/a" },
  { label: "Shadow max", value: (r) => formatPct(r.shadowMaxRoiPct) },
  { label: "Fee/buy", value: (r) => formatPct(r.feeToBuyPct) },
  { label: "Curve", value: (r) => r.independentCurve ? "local" : (r.curveSource || "n/a") },
  { label: "Error", value: (r) => r.buyError ? String(r.buyError).slice(0, 45) : (r.sellError ? String(r.sellError).slice(0, 45) : "") },
]));

async function scanCapsulesForShadowMarks(file, entries) {
  if (!entries.size || !fs.existsSync(file)) return;
  const stream = fs.createReadStream(file, { encoding: "utf8" });
  const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
  for await (const line of rl) {
    if (!line) continue;
    let row;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    const mint = row?.candidate?.mint || row?.mint;
    const entry = entries.get(mint);
    if (!entry || !Number.isFinite(entry.entryAtMs) || !Number.isFinite(entry.entryMarketCapUsd)) continue;
    const markTs = num(row.candidate_ready_ts_ms) ?? num(row.local_receive_ts_ms) ?? atMs(row);
    if (!Number.isFinite(markTs) || markTs <= entry.entryAtMs || markTs > entry.entryAtMs + horizonMs) continue;
    const mc = num(row?.canonical_state?.market_cap_usd) ?? num(row?.candidate?.market_cap_usd) ?? num(row.marketCapUsd);
    if (!Number.isFinite(mc) || mc <= 0 || mc > 500_000) continue;
    const roi = ((mc / entry.entryMarketCapUsd) - 1) * 100;
    const elapsed = markTs - entry.entryAtMs;
    entry.marks += 1;
    entry.maxRoiPct = entry.maxRoiPct == null ? roi : Math.max(entry.maxRoiPct, roi);
    entry.minRoiPct = entry.minRoiPct == null ? roi : Math.min(entry.minRoiPct, roi);
    if (entry.firstTpMs == null && roi >= tpPct) entry.firstTpMs = elapsed;
    if (entry.firstSlMs == null && roi <= slPct) entry.firstSlMs = elapsed;
  }
}
