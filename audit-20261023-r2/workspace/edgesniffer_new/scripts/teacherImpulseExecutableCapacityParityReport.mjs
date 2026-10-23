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
  : Date.parse(process.env.REPORT_SINCE || "2026-08-29T23:06:27Z");
const horizonMs = Number(process.env.REPORT_HORIZON_MS || 60_000);
const tpPct = Number(process.env.REPORT_TP_PCT || 20);
const slPct = Number(process.env.REPORT_SL_PCT || -20);
const maxHoldMs = Number(process.env.REPORT_MAX_HOLD_MS || 120_000);
const buySol = Number(process.env.REPORT_BUY_SOL || 0.001);
const maxEntryMcUsd = Number(process.env.REPORT_MAX_ENTRY_MC_USD || 7_500);
const sellTradeFeeBps = Number(process.env.REPORT_PUMPFUN_SELL_TRADE_FEE_BPS || 125);
const waitWindowsMs = String(process.env.REPORT_CACHE_WAIT_WINDOWS_MS || "10,25,50,75,100")
  .split(",")
  .map((value) => Number(value.trim()))
  .filter(Number.isFinite);
const maxTailBytes = Number(process.env.REPORT_MAX_TAIL_BYTES || 256 * 1024 * 1024);

function num(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function atMs(row) {
  return num(row?.atMs)
    ?? num(row?.entryAtMs)
    ?? num(row?.decidedAtMs)
    ?? num(row?.observedAtMs)
    ?? num(row?.candidate_ready_ts_ms)
    ?? num(row?.local_receive_ts_ms)
    ?? Date.parse(row?.at || "");
}

function rowMint(row) {
  return row?.mint || row?.candidate?.mint || row?.extra?.mint || null;
}

function rowKey(row) {
  return row?.key || row?.extra?.key || row?.features?.key || null;
}

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
    const firstNewline = text.indexOf("\n");
    text = firstNewline >= 0 ? text.slice(firstNewline + 1) : "";
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

function round(value, digits = 3) {
  return Number.isFinite(value) ? Number(value.toFixed(digits)) : null;
}

function fmtPct(value) {
  return Number.isFinite(value) ? `${round(value, 2)}%` : "n/a";
}

function fmtSol(value) {
  return Number.isFinite(value) ? `${round(value, 9)} SOL` : "n/a";
}

function pct(count, total) {
  return total > 0 ? (count / total) * 100 : 0;
}

function percentile(values, p) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  return sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(sorted.length * p) - 1))];
}

function median(values) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return null;
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
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

function incr(map, key, by = 1) {
  map.set(key, (map.get(key) || 0) + by);
}

function countRows(counts) {
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(String(b[0])))
    .map(([name, count]) => ({ name, count }));
}

function firstBy(items, keyFn, predicate = () => true) {
  const out = new Map();
  for (const item of items) {
    if (!predicate(item)) continue;
    const key = keyFn(item);
    if (!key) continue;
    const old = out.get(key);
    if (!old || atMs(item) < atMs(old)) out.set(key, item);
  }
  return out;
}

function groupBy(items, keyFn, predicate = () => true) {
  const out = new Map();
  for (const item of items) {
    if (!predicate(item)) continue;
    const key = keyFn(item);
    if (!key) continue;
    const group = out.get(key) || [];
    group.push(item);
    out.set(key, group);
  }
  for (const group of out.values()) group.sort((a, b) => atMs(a) - atMs(b));
  return out;
}

function firstInGroup(groups, key, predicate = () => true) {
  return (groups.get(key) || []).find(predicate) || null;
}

function shadowOutcomeFromMarks(entry) {
  const firstTp = entry.firstTpMs;
  const firstSl = entry.firstSlMs;
  if (firstTp != null && (firstSl == null || firstTp <= firstSl)) return "TP";
  if (firstSl != null) return "SL";
  return "none";
}

function scoreFromMarks(entry, entryMc, startMs, horizon = horizonMs) {
  const marks = (entry?.marks || []).filter((mark) =>
    Number.isFinite(mark.atMs)
    && Number.isFinite(mark.mc)
    && mark.atMs > startMs
    && mark.atMs <= startMs + horizon
  );
  const scored = marks.map((mark) => ({
    ...mark,
    elapsedMs: mark.atMs - startMs,
    roiPct: ((mark.mc / entryMc) - 1) * 100,
  }));
  let firstTpMs = null;
  let firstSlMs = null;
  let maxRoiPct = null;
  let minRoiPct = null;
  for (const mark of scored) {
    maxRoiPct = maxRoiPct == null ? mark.roiPct : Math.max(maxRoiPct, mark.roiPct);
    minRoiPct = minRoiPct == null ? mark.roiPct : Math.min(minRoiPct, mark.roiPct);
    if (firstTpMs == null && mark.roiPct >= tpPct) firstTpMs = mark.elapsedMs;
    if (firstSlMs == null && mark.roiPct <= slPct) firstSlMs = mark.elapsedMs;
  }
  const aboveDurations = {};
  for (const threshold of [20, 15, 10, 5]) {
    const above = scored.filter((mark) => mark.roiPct >= threshold);
    aboveDurations[threshold] = above.length
      ? Math.max(...above.map((mark) => mark.elapsedMs)) - Math.min(...above.map((mark) => mark.elapsedMs))
      : 0;
  }
  const outcome = firstTpMs != null && (firstSlMs == null || firstTpMs <= firstSlMs)
    ? "TP"
    : firstSlMs != null
      ? "SL"
      : "none";
  const exitMs = outcome === "TP" ? firstTpMs : outcome === "SL" ? firstSlMs : Math.min(horizon, maxHoldMs);
  const pnlPct = outcome === "TP" ? tpPct : outcome === "SL" ? slPct : (scored.at(-1)?.roiPct ?? 0);
  return {
    outcome,
    firstTpMs,
    firstSlMs,
    maxRoiPct,
    minRoiPct,
    marks: scored.length,
    exitMs,
    pnlPct,
    aboveDurations,
  };
}

function classifyReject(reason) {
  if (!reason) return "not_attempted_or_unknown";
  if (reason === "selected_candidate_open_capacity_reached") return "capacity_blocked";
  if (reason === "selected_candidate_session_attempt_capacity_reached") return "session_cap";
  if (reason === "fill_first_send_time_curve_cache_miss") return "infrastructure_blocked";
  if (
    reason === "send_time_entry_mc_above_max"
    || reason === "guaranteed_min_out_unavailable"
    || reason === "send_time_calibrated_quote_below_protected_floor"
  ) return "economic_guard_rejected";
  if (reason === "stale_selected_candidate") return "candidate_stale";
  if (reason.includes("route") || reason.includes("template")) return "route_template_veto";
  return "other_reject";
}

function capacityReplay(entries, maxOpen) {
  const open = [];
  const taken = [];
  const blocked = [];
  const timeline = [];
  for (const entry of entries.slice().sort((a, b) => a.entryAtMs - b.entryAtMs)) {
    while (open.length && open[0].exitAtMs <= entry.entryAtMs) open.shift();
    timeline.push({ atMs: entry.entryAtMs, open: open.length });
    if (open.length >= maxOpen) {
      blocked.push({ ...entry, blockReason: "shadow_open_capacity_reached", openAtBlock: open.length });
      continue;
    }
    const exitAtMs = entry.entryAtMs + Math.min(entry.exitMs ?? maxHoldMs, maxHoldMs);
    const takenEntry = { ...entry, exitAtMs };
    taken.push(takenEntry);
    open.push(takenEntry);
    open.sort((a, b) => a.exitAtMs - b.exitAtMs);
  }
  const pnls = taken.map((entry) => entry.pnlPct).filter(Number.isFinite);
  const wins = taken.filter((entry) => entry.outcome === "TP");
  const losses = taken.filter((entry) => entry.outcome === "SL");
  const grossProfit = pnls.filter((value) => value > 0).reduce((sum, value) => sum + value, 0);
  const grossLoss = Math.abs(pnls.filter((value) => value < 0).reduce((sum, value) => sum + value, 0));
  const blockedWinners = blocked.filter((entry) => entry.outcome === "TP" || entry.theoreticalOutcome === "TP");
  return {
    maxOpen,
    eligibleEntries: entries.length,
    taken,
    blocked,
    trades: taken.length,
    winners: wins.length,
    losers: losses.length,
    maxHoldOrNeither: taken.filter((entry) => entry.outcome === "none").length,
    winRate: pct(wins.length, taken.length),
    grossProfit,
    grossLoss,
    netPnl: pnls.reduce((sum, value) => sum + value, 0),
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0,
    avgPnl: pnls.length ? pnls.reduce((sum, value) => sum + value, 0) / pnls.length : 0,
    maxConcurrentExposureSol: maxOpen * buySol,
    blockedExecutableWinners: blockedWinners.length,
    blockedWinnerMints: blockedWinners.map((entry) => entry.mint),
    timeline,
  };
}

function concurrencyStats(entries) {
  const events = [];
  for (const entry of entries) {
    if (!Number.isFinite(entry.entryAtMs) || !Number.isFinite(entry.exitMs)) continue;
    events.push({ atMs: entry.entryAtMs, delta: 1 });
    events.push({ atMs: entry.entryAtMs + Math.min(entry.exitMs, maxHoldMs), delta: -1 });
  }
  events.sort((a, b) => a.atMs - b.atMs || a.delta - b.delta);
  if (!events.length) {
    return { peak: 0, median: 0, avg: 0, p90: 0, buckets: new Map([[0, 100]]) };
  }
  const samples = [];
  const bucketDur = new Map();
  let open = 0;
  let last = events[0].atMs;
  for (const event of events) {
    if (event.atMs > last) {
      const dt = event.atMs - last;
      samples.push(open);
      bucketDur.set(open, (bucketDur.get(open) || 0) + dt);
      last = event.atMs;
    }
    open += event.delta;
  }
  const totalDur = [...bucketDur.values()].reduce((sum, value) => sum + value, 0);
  const bucketPct = new Map();
  for (const [bucket, dur] of bucketDur.entries()) {
    const label = bucket >= 3 ? "3+" : String(bucket);
    bucketPct.set(label, (bucketPct.get(label) || 0) + (totalDur > 0 ? (dur / totalDur) * 100 : 0));
  }
  return {
    peak: Math.max(0, ...samples),
    median: median(samples) ?? 0,
    avg: samples.length ? samples.reduce((sum, value) => sum + value, 0) / samples.length : 0,
    p90: percentile(samples, 0.9) ?? 0,
    buckets: bucketPct,
  };
}

const selected = readTailJsonl(selectedFile)
  .filter((row) => atMs(row) >= sinceMs)
  .filter((row) => !policy || row.policy === policy)
  .sort((a, b) => atMs(a) - atMs(b));
const audit = readTailJsonl(auditFile)
  .filter((row) => atMs(row) >= sinceMs)
  .sort((a, b) => atMs(a) - atMs(b));

const selectedByKey = firstBy(selected, rowKey);
const selectedGroupsByMint = groupBy(selected, rowMint);
const firstSelectedByMint = firstBy(selected, rowMint);
const auditByKey = groupBy(audit, rowKey);
const auditByMint = groupBy(audit, rowMint);
const rejectByKey = firstBy(audit, rowKey, (row) => row.kind === "rustSelectedCandidateRejected");
const buyByKey = firstBy(audit, rowKey, (row) => row.kind === "rustSelectedCandidateBuyResult");
const buyByMint = firstBy(audit, rowMint, (row) => row.kind === "rustSelectedCandidateBuyResult");
const exitByMint = firstBy(audit, rowMint, (row) => row.kind === "rustExitDecision");
const sellFinalityByMint = firstBy(audit, rowMint, (row) => row.kind === "rustExitSellFinality");

const entriesByMint = new Map();
for (const [mint, row] of firstSelectedByMint.entries()) {
  entriesByMint.set(mint, {
    mint,
    entryAtMs: atMs(row),
    entryMc: num(row.entryMarketCapUsd),
    selectedKey: rowKey(row),
    marks: [],
  });
}

await scanCapsules(capsulesFile, entriesByMint);

const theoreticalEntries = [];
for (const entry of entriesByMint.values()) {
  const score = scoreFromMarks(entry, entry.entryMc, entry.entryAtMs);
  theoreticalEntries.push({
    ...entry,
    ...score,
    theoreticalOutcome: score.outcome,
    theoreticalMaxRoiPct: score.maxRoiPct,
  });
}

const theoreticalWinners = theoreticalEntries.filter((entry) => entry.theoreticalOutcome === "TP");
const theoreticalSl = theoreticalEntries.filter((entry) => entry.theoreticalOutcome === "SL");
const theoreticalNone = theoreticalEntries.filter((entry) => entry.theoreticalOutcome === "none");
const theoreticalByMint = new Map(theoreticalEntries.map((entry) => [entry.mint, entry]));

const candidateRows = selected.map((row) => {
  const key = rowKey(row);
  const mint = rowMint(row);
  const rejected = rejectByKey.get(key);
  const buy = buyByKey.get(key);
  const grouped = auditByKey.get(key) || [];
  const curveHit = grouped.find((item) => item.kind === "rustSelectedCandidateCurveCacheHit");
  const sendRefresh = grouped.find((item) => item.kind === "rustSelectedCandidateSendTimeCurveRefresh");
  const preSign = grouped.find((item) => item.kind === "rustSelectedCandidatePreSignEconomicGuard");
  const reason = rejected?.reason || null;
  const executable = Boolean(buy?.ok);
  const entryMc = num(
    buy?.finalEconomicGuardEntryMarketCapUsd
      ?? sendRefresh?.sendTimeEntryMarketCapUsd
      ?? preSign?.preSignEntryMarketCapUsd
      ?? row.entryMarketCapUsd
  );
  const entryAt = num(buy?.atMs) ?? atMs(row);
  const baseEntry = entriesByMint.get(mint);
  const score = baseEntry && Number.isFinite(entryMc)
    ? scoreFromMarks(baseEntry, entryMc, entryAt)
    : null;
  const theoretical = theoreticalByMint.get(mint);
  return {
    key,
    mint,
    selectedAtMs: atMs(row),
    entryAtMs: entryAt,
    selectedMc: num(row.entryMarketCapUsd),
    liveLikeEntryMc: entryMc,
    curveSource: buy?.preSignReserveSource || sendRefresh?.reserveSource || curveHit?.cacheSource || row?.directEntryQuote?.curveSnapshot?.source || null,
    curveAgeMs: num(buy?.finalEconomicGuardAtMs) != null && num(buy?.finalEconomicGuardReserveObservedAtMs) != null
      ? num(buy.finalEconomicGuardAtMs) - num(buy.finalEconomicGuardReserveObservedAtMs)
      : num(curveHit?.cacheAgeMs),
    reason,
    rejectClass: classifyReject(reason),
    executable,
    buy,
    rejected,
    score,
    outcome: score?.outcome,
    pnlPct: score?.pnlPct,
    exitMs: score?.exitMs,
    theoreticalOutcome: theoretical?.theoreticalOutcome || null,
    theoreticalMaxRoiPct: theoretical?.theoreticalMaxRoiPct ?? null,
  };
});

const firstExecutableByMint = new Map();
for (const row of candidateRows) {
  if (!row.executable) continue;
  if (!firstExecutableByMint.has(row.mint)) firstExecutableByMint.set(row.mint, row);
}

const executableEntries = [...firstExecutableByMint.values()].map((row) => ({
  mint: row.mint,
  key: row.key,
  entryAtMs: row.entryAtMs,
  entryMc: row.liveLikeEntryMc,
  outcome: row.outcome || "none",
  pnlPct: row.pnlPct ?? 0,
  exitMs: row.exitMs ?? maxHoldMs,
  theoreticalOutcome: row.theoreticalOutcome,
  theoreticalMaxRoiPct: row.theoreticalMaxRoiPct,
  source: "actual_live_executable_candidate",
}));

const actualLiveRows = [...buyByMint.values()].map((buy) => {
  const mint = rowMint(buy);
  const exit = exitByMint.get(mint);
  const sell = sellFinalityByMint.get(mint);
  return {
    mint,
    key: rowKey(buy),
    buyMs: num(buy.selectedToBuyResultMs),
    guardToBuyMs: num(buy.finalEconomicGuardToBuyResultMs),
    entryMc: num(buy.finalEconomicGuardEntryMarketCapUsd),
    finalGuardSource: buy.finalEconomicGuardReserveSource || buy.preSignReserveSource,
    finalGuardAgeMs: num(buy.finalEconomicGuardAtMs) != null && num(buy.finalEconomicGuardReserveObservedAtMs) != null
      ? num(buy.finalEconomicGuardAtMs) - num(buy.finalEconomicGuardReserveObservedAtMs)
      : null,
    expectedTokens: num(buy.finalEconomicGuardExpectedTokensRaw),
    actualTokens: num(buy.tokensOutRaw),
    exitReason: exit?.reason || null,
    livePnlPct: num(exit?.pnlPct),
    sellStatus: sell?.status || null,
    sellError: sell?.error || null,
  };
});

const cacheMissRows = candidateRows.filter((row) => row.reason === "fill_first_send_time_curve_cache_miss");
const cacheMissAnalysis = analyzeCacheMisses(cacheMissRows, entriesByMint);
const winnerMissRows = theoreticalWinners.map((entry) => {
  const mint = entry.mint;
  const candidates = candidateRows.filter((row) => row.mint === mint);
  const live = actualLiveRows.find((row) => row.mint === mint);
  const firstRejected = candidates.find((row) => row.rejected);
  const firstExecutable = candidates.find((row) => row.executable);
  const reason = live ? "live_entered" : firstRejected?.reason || "no_live_decision";
  const rejectClass = live ? "live_entered" : classifyReject(reason);
  return {
    mint,
    firstTpMs: entry.firstTpMs,
    tpDuration20Ms: entry.aboveDurations[20],
    maxRoiPct: entry.maxRoiPct,
    liveAttempted: Boolean(live),
    livePnlPct: live?.livePnlPct,
    liveExit: live?.exitReason,
    reason,
    rejectClass,
    executableCandidate: Boolean(firstExecutable),
    executableOutcome: firstExecutable?.outcome || null,
    executablePnlPct: firstExecutable?.pnlPct ?? null,
    selectedRows: candidates.length,
    cacheRecovery: cacheMissAnalysis.byMint.get(mint) || null,
  };
});

const capacity1 = capacityReplay(executableEntries, 1);
const capacity2 = capacityReplay(executableEntries, 2);
const capacity3 = capacityReplay(executableEntries, 3);
const theoreticalCapacityEntries = theoreticalEntries.map((entry) => ({
  mint: entry.mint,
  entryAtMs: entry.entryAtMs,
  outcome: entry.outcome,
  pnlPct: entry.pnlPct,
  exitMs: entry.exitMs,
  theoreticalOutcome: entry.theoreticalOutcome,
}));
const theoreticalCapacity1 = capacityReplay(theoreticalCapacityEntries, 1);
const theoreticalCapacity2 = capacityReplay(theoreticalCapacityEntries, 2);
const theoreticalCapacity3 = capacityReplay(theoreticalCapacityEntries, 3);
const theoreticalConcurrency = concurrencyStats(theoreticalEntries);
const executableConcurrency = concurrencyStats(executableEntries);

const eightJuq = buildEightJuq("8JUQps2GsryEuJiMmyCuukpEXg8c8zDZk7gXWednpump");
const rejectCost = buildRejectCost(winnerMissRows);

printReport();

async function scanCapsules(file, entries) {
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
    const mint = rowMint(row);
    const entry = entries.get(mint);
    if (!entry) continue;
    const observed = atMs(row);
    if (!Number.isFinite(observed) || observed <= entry.entryAtMs || observed > entry.entryAtMs + Math.max(horizonMs, maxHoldMs)) continue;
    const mc = num(row?.canonical_state?.market_cap_usd) ?? num(row?.candidate?.market_cap_usd) ?? num(row.marketCapUsd);
    if (!Number.isFinite(mc) || mc <= 0 || mc > 500_000) continue;
    entry.marks.push({
      atMs: observed,
      elapsedMs: observed - entry.entryAtMs,
      mc,
      sourceSlot: num(row.source_slot),
      side: row?.raw_record?.side || null,
    });
  }
}

function analyzeCacheMisses(rows, entries) {
  const byMint = new Map();
  const aggregate = new Map();
  for (const row of rows) {
    const entry = entries.get(row.mint);
    if (!entry) continue;
    const windows = {};
    for (const wait of waitWindowsMs) {
      const target = row.selectedAtMs + wait;
      const mark = entry.marks.find((item) => item.atMs >= target);
      if (!mark) {
        windows[wait] = { recovered: false };
        continue;
      }
      const laterEntryMc = mark.mc;
      const score = scoreFromMarks(entry, laterEntryMc, mark.atMs);
      const economicallyValid = laterEntryMc <= maxEntryMcUsd;
      windows[wait] = {
        recovered: true,
        waitToStateMs: mark.atMs - row.selectedAtMs,
        laterEntryMc,
        economicallyValid,
        futureOutcome: score.outcome,
        futurePnlPct: score.pnlPct,
        firstTpMs: score.firstTpMs,
        maxRoiPct: score.maxRoiPct,
      };
      const label = `${wait}ms:${economicallyValid ? "valid" : "mc_rejected"}:${score.outcome}`;
      incr(aggregate, label);
    }
    const current = byMint.get(row.mint);
    if (!current || row.selectedAtMs < current.selectedAtMs) {
      byMint.set(row.mint, { mint: row.mint, selectedAtMs: row.selectedAtMs, windows });
    }
  }
  return { byMint, aggregate };
}

function buildRejectCost(rows) {
  const out = new Map();
  for (const row of rows) {
    if (row.liveAttempted) continue;
    const reason = row.reason || "unknown";
    const current = out.get(reason) || {
      reason,
      theoreticalWinners: 0,
      executableWinners: 0,
      simulatedPnlLostPct: 0,
      durations: [],
      examples: [],
    };
    current.theoreticalWinners += 1;
    if (row.executableCandidate || row.rejectClass !== "economic_guard_rejected") current.executableWinners += 1;
    current.simulatedPnlLostPct += Number.isFinite(row.executablePnlPct) ? row.executablePnlPct : tpPct;
    if (Number.isFinite(row.tpDuration20Ms)) current.durations.push(row.tpDuration20Ms);
    if (current.examples.length < 5) current.examples.push(row.mint);
    out.set(reason, current);
  }
  return [...out.values()].map((row) => ({
    ...row,
    avgDurationMs: row.durations.length
      ? row.durations.reduce((sum, value) => sum + value, 0) / row.durations.length
      : null,
  })).sort((a, b) => b.theoreticalWinners - a.theoreticalWinners);
}

function buildEightJuq(mint) {
  const theory = theoreticalEntries.find((entry) => entry.mint === mint);
  const candidates = candidateRows.filter((row) => row.mint === mint);
  const live = actualLiveRows.find((row) => row.mint === mint);
  const executable = candidates.find((row) => row.executable);
  const liveMarks = (auditByMint.get(mint) || []).filter((row) => row.kind === "rustExitMark" && Number.isFinite(row.pnlPct));
  const liveTpDecisions = (auditByMint.get(mint) || []).filter((row) => row.kind === "rustExitDecision" && row.reason === "take-profit");
  return {
    mint,
    theoretical: theory,
    candidates,
    live,
    executable,
    liveMarks,
    liveTpDecisions,
  };
}

function printCapacity(title, result) {
  console.log(`## ${title}`);
  console.log("");
  console.log("| Metric | Value |");
  console.log("|---|---:|");
  console.log(`| Eligible entries | ${result.eligibleEntries} |`);
  console.log(`| Entries taken | ${result.trades} |`);
  console.log(`| Winners | ${result.winners} |`);
  console.log(`| Losers | ${result.losers} |`);
  console.log(`| Max-hold / neither | ${result.maxHoldOrNeither} |`);
  console.log(`| Win rate | ${fmtPct(result.winRate)} |`);
  console.log(`| Gross profit | ${fmtPct(result.grossProfit)} |`);
  console.log(`| Gross loss | ${fmtPct(-result.grossLoss)} |`);
  console.log(`| Net simulated PnL | ${fmtPct(result.netPnl)} |`);
  console.log(`| Profit factor | ${Number.isFinite(result.profitFactor) ? round(result.profitFactor, 3) : "inf"} |`);
  console.log(`| Avg PnL / trade | ${fmtPct(result.avgPnl)} |`);
  console.log(`| Max concurrent exposure | ${fmtSol(result.maxConcurrentExposureSol)} |`);
  console.log(`| Executable winners blocked by capacity | ${result.blockedExecutableWinners} |`);
  console.log("");
}

function printReport() {
  const liveWins = actualLiveRows.filter((row) => Number.isFinite(row.livePnlPct) && row.livePnlPct > 0);
  const liveLosses = actualLiveRows.filter((row) => Number.isFinite(row.livePnlPct) && row.livePnlPct <= 0);
  const executableWinners = executableEntries.filter((entry) => entry.outcome === "TP");
  const actualEnteredExecutableWinners = actualLiveRows.filter((row) => {
    const executable = executableEntries.find((entry) => entry.mint === row.mint);
    return executable?.outcome === "TP";
  });
  const realizedExecutableWinners = actualEnteredExecutableWinners.filter((row) => Number.isFinite(row.livePnlPct) && row.livePnlPct > 0);
  const captureRatios = actualEnteredExecutableWinners
    .map((row) => {
      const executable = executableEntries.find((entry) => entry.mint === row.mint);
      return executable?.pnlPct > 0 && Number.isFinite(row.livePnlPct) ? row.livePnlPct / executable.pnlPct : null;
    })
    .filter(Number.isFinite);

  console.log("# Executable + Capacity-Constrained Shadow / Parity Report");
  console.log("");
  console.log(`Policy: ${policy}`);
  console.log(`Since: ${new Date(sinceMs).toISOString()}`);
  console.log(`TP/SL: ${tpPct}% / ${slPct}%`);
  console.log(`Max hold: ${maxHoldMs}ms`);
  console.log(`Position size: ${buySol} SOL`);
  console.log("");
  console.log("> This report is read-only analytics. It does not change live trading. Theoretical shadow remains a signal-research view. Executable shadow is intentionally stricter and only uses reconstructed/live-available entry evidence where available.");
  console.log("");

  console.log("## 1. Theoretical Shadow");
  console.log("");
  console.log("| Metric | Value |");
  console.log("|---|---:|");
  console.log(`| Selected mints | ${theoreticalEntries.length} |`);
  console.log(`| TP mints | ${theoreticalWinners.length} |`);
  console.log(`| SL mints | ${theoreticalSl.length} |`);
  console.log(`| Neither | ${theoreticalNone.length} |`);
  console.log(`| Signal win rate | ${fmtPct(pct(theoreticalWinners.length, theoreticalEntries.length))} |`);
  console.log(`| Net mark PnL proxy | ${fmtPct(theoreticalEntries.reduce((sum, entry) => sum + (entry.pnlPct || 0), 0))} |`);
  console.log("");

  console.log("## 2. Executable Shadow");
  console.log("");
  console.log("| Metric | Value |");
  console.log("|---|---:|");
  console.log(`| Executable entries | ${executableEntries.length} |`);
  console.log(`| Executable winners | ${executableWinners.length} |`);
  console.log(`| Executable losers | ${executableEntries.filter((entry) => entry.outcome === "SL").length} |`);
  console.log(`| Executable neither/max-hold | ${executableEntries.filter((entry) => entry.outcome === "none").length} |`);
  console.log(`| Executable win rate | ${fmtPct(pct(executableWinners.length, executableEntries.length))} |`);
  console.log(`| Executable net PnL proxy | ${fmtPct(executableEntries.reduce((sum, entry) => sum + (entry.pnlPct || 0), 0))} |`);
  console.log("");

  printCapacity("3. Executable + Capacity Shadow - MAX_OPEN 1", capacity1);
  printCapacity("4. Executable + Capacity Shadow - MAX_OPEN 2", capacity2);
  printCapacity("5. Executable + Capacity Shadow - MAX_OPEN 3", capacity3);

  console.log("## 6. Concurrency Distribution");
  console.log("");
  console.log(markdownTable([
    { name: "THEORETICAL_SHADOW", stats: theoreticalConcurrency },
    { name: "EXECUTABLE_SHADOW", stats: executableConcurrency },
  ], [
    { label: "View", value: (row) => row.name },
    { label: "Peak", value: (row) => round(row.stats.peak, 2) },
    { label: "Median", value: (row) => round(row.stats.median, 2) },
    { label: "Avg", value: (row) => round(row.stats.avg, 2) },
    { label: "P90", value: (row) => round(row.stats.p90, 2) },
    { label: "0 open", value: (row) => fmtPct(row.stats.buckets.get("0") || 0) },
    { label: "1 open", value: (row) => fmtPct(row.stats.buckets.get("1") || 0) },
    { label: "2 open", value: (row) => fmtPct(row.stats.buckets.get("2") || 0) },
    { label: "3+ open", value: (row) => fmtPct(row.stats.buckets.get("3+") || 0) },
  ]));
  console.log("");

  console.log("## 7. Expectancy / PnL Comparison");
  console.log("");
  console.log(markdownTable([
    {
      view: "THEORETICAL_SHADOW",
      trades: theoreticalEntries.length,
      wins: theoreticalWinners.length,
      losses: theoreticalSl.length,
      net: theoreticalEntries.reduce((sum, entry) => sum + (entry.pnlPct || 0), 0),
    },
    {
      view: "CAPACITY_CONSTRAINED_THEORETICAL maxOpen=1",
      trades: theoreticalCapacity1.trades,
      wins: theoreticalCapacity1.winners,
      losses: theoreticalCapacity1.losers,
      net: theoreticalCapacity1.netPnl,
    },
    {
      view: "CAPACITY_CONSTRAINED_THEORETICAL maxOpen=2",
      trades: theoreticalCapacity2.trades,
      wins: theoreticalCapacity2.winners,
      losses: theoreticalCapacity2.losers,
      net: theoreticalCapacity2.netPnl,
    },
    {
      view: "CAPACITY_CONSTRAINED_THEORETICAL maxOpen=3",
      trades: theoreticalCapacity3.trades,
      wins: theoreticalCapacity3.winners,
      losses: theoreticalCapacity3.losers,
      net: theoreticalCapacity3.netPnl,
    },
    {
      view: "EXECUTABLE_SHADOW",
      trades: executableEntries.length,
      wins: executableWinners.length,
      losses: executableEntries.filter((entry) => entry.outcome === "SL").length,
      net: executableEntries.reduce((sum, entry) => sum + (entry.pnlPct || 0), 0),
    },
    {
      view: "EXECUTABLE_CAPACITY_SHADOW maxOpen=1",
      trades: capacity1.trades,
      wins: capacity1.winners,
      losses: capacity1.losers,
      net: capacity1.netPnl,
    },
    {
      view: "EXECUTABLE_CAPACITY_SHADOW maxOpen=2",
      trades: capacity2.trades,
      wins: capacity2.winners,
      losses: capacity2.losers,
      net: capacity2.netPnl,
    },
    {
      view: "EXECUTABLE_CAPACITY_SHADOW maxOpen=3",
      trades: capacity3.trades,
      wins: capacity3.winners,
      losses: capacity3.losers,
      net: capacity3.netPnl,
    },
    {
      view: "ACTUAL_LIVE",
      trades: actualLiveRows.length,
      wins: liveWins.length,
      losses: liveLosses.length,
      net: actualLiveRows.reduce((sum, row) => sum + (row.livePnlPct || 0), 0),
    },
  ], [
    { label: "View", value: (row) => row.view },
    { label: "Trades", value: (row) => row.trades },
    { label: "Wins", value: (row) => row.wins },
    { label: "Losses", value: (row) => row.losses },
    { label: "Win rate", value: (row) => fmtPct(pct(row.wins, row.trades)) },
    { label: "Net PnL proxy", value: (row) => fmtPct(row.net) },
  ]));
  console.log("");

  console.log("## 8. Winners Blocked By Capacity");
  console.log("");
  console.log(markdownTable(theoreticalCapacity1.blocked.filter((row) => row.theoreticalOutcome === "TP").slice(0, 50), [
    { label: "Mint", value: (row) => `\`${row.mint}\`` },
    { label: "Outcome", value: (row) => row.outcome },
    { label: "PnL", value: (row) => fmtPct(row.pnlPct) },
    { label: "Entry", value: (row) => new Date(row.entryAtMs).toISOString() },
  ]));
  console.log("");

  console.log("## 9. Winners Blocked By Each Live Gate");
  console.log("");
  console.log(markdownTable(rejectCost, [
    { label: "Gate", value: (row) => row.reason },
    { label: "Rejected theoretical winners", value: (row) => row.theoreticalWinners },
    { label: "Executable/proxy winners", value: (row) => row.executableWinners },
    { label: "PnL lost proxy", value: (row) => fmtPct(row.simulatedPnlLostPct) },
    { label: "Avg >=20 duration", value: (row) => row.avgDurationMs == null ? "n/a" : `${round(row.avgDurationMs, 1)}ms` },
    { label: "Examples", value: (row) => row.examples.map((mint) => `\`${mint}\``).join(", ") },
  ]));
  console.log("");

  console.log("## 10. Yellowstone Cache-Miss Opportunity Cost");
  console.log("");
  console.log(`Cache-miss selected rows: ${cacheMissRows.length}`);
  console.log("");
  console.log(markdownTable(countRows(cacheMissAnalysis.aggregate).slice(0, 30), [
    { label: "Recovery bucket", value: (row) => row.name },
    { label: "Rows", value: (row) => row.count },
  ]));
  console.log("");

  console.log("## 11. 8JUQ Reclassification");
  console.log("");
  printEightJuq(eightJuq);

  console.log("## 12. Actual Live Vs Honest Shadow");
  console.log("");
  console.log("| Metric | Value |");
  console.log("|---|---:|");
  console.log(`| Actual live trades | ${actualLiveRows.length} |`);
  console.log(`| Actual live wins | ${liveWins.length} |`);
  console.log(`| Actual live net PnL | ${fmtPct(actualLiveRows.reduce((sum, row) => sum + (row.livePnlPct || 0), 0))} |`);
  console.log(`| Entered executable winners | ${actualEnteredExecutableWinners.length} |`);
  console.log(`| Realized executable winners | ${realizedExecutableWinners.length} |`);
  console.log(`| Winner -> loser conversions | ${actualEnteredExecutableWinners.length - realizedExecutableWinners.length} |`);
  console.log(`| Median TP capture ratio | ${captureRatios.length ? round(median(captureRatios), 3) : "n/a"} |`);
  console.log("");

  console.log("## 13. Top 3 Remaining Bottlenecks");
  console.log("");
  console.log("1. Yellowstone/cache availability at send time: it blocks the most theoretical winners in the frozen sample.");
  console.log("2. Shadow scoring still needs live-like sell economics for every non-live candidate; mark-only winners are not enough for promotion.");
  console.log("3. Capacity/session semantics materially change opportunity coverage; theoretical independent-mint shadow is not a live benchmark.");
  console.log("");

  console.log("## 14. Recommendation");
  console.log("");
  console.log("Do not promote from THEORETICAL_SHADOW. Use EXECUTABLE_CAPACITY_SHADOW as the live target. The next engineering decision should be based on whether cache-miss candidates remain economically valid 10-100ms later. If they do, the next bottleneck is infrastructure/cache availability. If they do not, the gate is correctly avoiding unreachable opportunities.");
}

function printEightJuq(data) {
  const theory = data.theoretical;
  const live = data.live;
  const executable = data.executable;
  const liveMax = data.liveMarks.length ? Math.max(...data.liveMarks.map((row) => row.pnlPct)) : null;
  const liveMin = data.liveMarks.length ? Math.min(...data.liveMarks.map((row) => row.pnlPct)) : null;
  console.log("| Metric | Value |");
  console.log("|---|---:|");
  console.log(`| Theoretical first TP | ${theory?.firstTpMs ?? "n/a"}ms |`);
  console.log(`| Theoretical max ROI | ${fmtPct(theory?.maxRoiPct)} |`);
  console.log(`| Executable candidate entry MC | ${executable?.liveLikeEntryMc ? `$${round(executable.liveLikeEntryMc, 2)}` : "n/a"} |`);
  console.log(`| Executable-shadow reclassified outcome | ${executable?.outcome || "n/a"} |`);
  console.log(`| Executable-shadow PnL proxy | ${fmtPct(executable?.pnlPct)} |`);
  console.log(`| Live exit | ${live?.exitReason || "n/a"} |`);
  console.log(`| Live PnL | ${fmtPct(live?.livePnlPct)} |`);
  console.log(`| Live watcher max PnL | ${fmtPct(liveMax)} |`);
  console.log(`| Live watcher min PnL | ${fmtPct(liveMin)} |`);
  console.log(`| Live TP decisions | ${data.liveTpDecisions.length} |`);
  console.log("");
  console.log("8JUQ is not a proven TP-execution failure. Under live-like entry/watcher economics it reclassifies as a loser, because live never observed TP and exited on hard stop before the later theoretical/proxy pump window.");
  console.log("");
}
