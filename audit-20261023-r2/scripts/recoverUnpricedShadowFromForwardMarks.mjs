import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

function parseArgs(argv) {
  const out = {};
  for (const arg of argv) {
    if (!arg.startsWith("--")) continue;
    const [key, ...rest] = arg.slice(2).split("=");
    out[key] = rest.join("=") || true;
  }
  return out;
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

function atMs(row) {
  return finite(row.requestedAtMs) ?? finite(row.atMs) ?? Date.parse(row.at || "");
}

function relativeDeltaPct(a, b) {
  if (!(a > 0) || !(b > 0)) return Infinity;
  return (Math.abs(a - b) / a) * 100;
}

function replay(trade) {
  const c = trade.config;
  const marks = trade.marks.sort((a, b) => a.atMs - b.atMs);
  let bestRoiPct = 0;
  let worstRoiPct = 0;

  for (const mark of marks) {
    bestRoiPct = Math.max(bestRoiPct, mark.roiPct);
    worstRoiPct = Math.min(worstRoiPct, mark.roiPct);
    let reason;
    if (mark.roiPct <= c.stopLossPct) reason = "stop_loss";
    else if (mark.roiPct >= c.takeProfitPct) reason = "take_profit";
    else if (bestRoiPct >= c.trailingStartPct && mark.roiPct <= bestRoiPct - c.trailingGivebackPct) {
      reason = "trailing_stop";
    }
    if (reason) return recoveredClose(trade, mark, reason, bestRoiPct, worstRoiPct);
  }

  const deadlineMs = trade.openedAtMs + c.maxHoldMs;
  const last = marks.filter((mark) => mark.atMs <= deadlineMs).at(-1);
  if (!last) return { recovered: false, reason: "no_compatible_mark_in_exit_window" };
  const markAgeMs = Math.max(0, deadlineMs - last.atMs);
  if (markAgeMs >= c.noUpdateExitMs) {
    return { recovered: false, reason: "still_unpriced_no_update", lastMarkAtMs: last.atMs, markAgeMs };
  }
  if (markAgeMs > c.maxExitMarkAgeMs) {
    return { recovered: false, reason: "still_unpriced_stale_exit", lastMarkAtMs: last.atMs, markAgeMs };
  }
  return recoveredClose(trade, { ...last, atMs: deadlineMs }, "time_exit", bestRoiPct, worstRoiPct, markAgeMs);
}

function recoveredClose(trade, mark, reason, bestRoiPct, worstRoiPct, forcedMarkAgeMs = 0) {
  const c = trade.config;
  const grossPnlFromRoiSol = c.paperBetSol * mark.roiPct / 100;
  const executionCostSol = c.paperBetSol * c.entryCostBps / 10_000
    + Math.max(0, c.paperBetSol + grossPnlFromRoiSol) * c.exitCostBps / 10_000
    + c.fixedRoundTripCostSol;
  return {
    recovered: true,
    reason,
    exitAt: new Date(mark.atMs).toISOString(),
    exitAtMs: mark.atMs,
    holdMs: Math.max(0, mark.atMs - trade.openedAtMs),
    markAgeMs: forcedMarkAgeMs,
    roiPct: mark.roiPct,
    bestRoiPct,
    worstRoiPct,
    directCurvePnlSol: mark.curvePnlSol,
    comparableGrossPnlSol: grossPnlFromRoiSol,
    comparableExecutionCostSol: executionCostSol,
    comparableNetPnlSol: grossPnlFromRoiSol - executionCostSol,
    forwardMarkAt: mark.originalAt,
    forwardOpenedAtMs: mark.forwardOpenedAtMs,
    openDeltaMs: mark.openDeltaMs,
    entryMarketCapDeltaPct: mark.entryMarketCapDeltaPct,
  };
}

const args = parseArgs(process.argv.slice(2));
const shadowFile = String(args.shadow || "");
const forwardFile = String(args.forward || "");
const outputFile = args.out ? String(args.out) : undefined;
const maxOpenDeltaMs = finite(args["max-open-delta-ms"]) ?? 2_000;
const maxEntryMarketCapDeltaPct = finite(args["max-entry-mc-delta-pct"]) ?? 2;

if (!shadowFile || !forwardFile) {
  throw new Error("Usage: node recoverUnpricedShadowFromForwardMarks.mjs --shadow=<shadow.jsonl> --forward=<forward.jsonl> [--out=<report.json>]");
}
for (const file of [shadowFile, forwardFile]) {
  if (!fs.existsSync(file)) throw new Error(`Missing input: ${file}`);
}

const rows = fs.readFileSync(shadowFile, "utf8").split(/\n/).filter(Boolean).flatMap((line) => {
  try { return [JSON.parse(line)]; } catch { return []; }
});
const opens = new Map();
const closes = new Map();
for (const row of rows) {
  if (row.event === "shadow_open" && row.tradeId) opens.set(row.tradeId, row);
  if (row.event === "shadow_close" && row.tradeId) closes.set(row.tradeId, row);
}

const targets = new Map();
const byMint = new Map();
for (const [tradeId, open] of opens) {
  const close = closes.get(tradeId);
  if (!close || close.priced === true) continue;
  const openedAtMs = finite(open.openedAtMs) ?? Date.parse(open.openedAt || open.at || "");
  const entryMarketCapUsd = finite(open.entryMarketCapUsd);
  const config = open.exits || open.position?.exits;
  if (!Number.isFinite(openedAtMs) || !entryMarketCapUsd || !config) continue;
  const trade = {
    tradeId,
    mint: open.mint,
    profile: open.profile,
    openedAt: new Date(openedAtMs).toISOString(),
    openedAtMs,
    entryMarketCapUsd,
    originalCloseReason: close.reason,
    originalCloseAt: close.at,
    config,
    marks: [],
    mintForwardMarks: 0,
    incompatibleOpeningMarks: 0,
  };
  targets.set(tradeId, trade);
  const list = byMint.get(open.mint) || [];
  list.push(trade);
  byMint.set(open.mint, list);
}

const stream = fs.createReadStream(forwardFile, { encoding: "utf8", highWaterMark: 4 * 1024 * 1024 });
const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
let forwardLines = 0;
let malformedForwardLines = 0;
for await (const line of rl) {
  forwardLines += 1;
  let row;
  try { row = JSON.parse(line); } catch { malformedForwardLines += 1; continue; }
  const candidates = byMint.get(row.mint);
  if (!candidates) continue;
  const timestamp = atMs(row);
  const roiPct = finite(row.curveRoiPct);
  if (!Number.isFinite(timestamp) || roiPct == null || row.status !== "ok") continue;
  for (const trade of candidates) {
    trade.mintForwardMarks += 1;
    const forwardOpenedAtMs = finite(row.openedAtMs);
    const forwardEntryMc = finite(row.entryMarketCapUsd);
    const openDeltaMs = forwardOpenedAtMs == null ? Infinity : Math.abs(forwardOpenedAtMs - trade.openedAtMs);
    const entryMarketCapDeltaPct = relativeDeltaPct(trade.entryMarketCapUsd, forwardEntryMc);
    if (openDeltaMs > maxOpenDeltaMs || entryMarketCapDeltaPct > maxEntryMarketCapDeltaPct) {
      trade.incompatibleOpeningMarks += 1;
      continue;
    }
    if (timestamp < trade.openedAtMs || timestamp > trade.openedAtMs + trade.config.maxHoldMs) continue;
    trade.marks.push({
      atMs: timestamp,
      originalAt: row.at,
      roiPct,
      curvePnlSol: finite(row.curvePnlSol),
      forwardOpenedAtMs,
      openDeltaMs,
      entryMarketCapDeltaPct,
    });
  }
}

const recoveredTrades = [];
for (const trade of targets.values()) {
  let result;
  if (trade.mintForwardMarks === 0) result = { recovered: false, reason: "mint_absent_from_forward_marks" };
  else if (trade.marks.length === 0 && trade.incompatibleOpeningMarks > 0) {
    result = { recovered: false, reason: "forward_marks_belong_to_different_opening" };
  } else result = replay(trade);
  recoveredTrades.push({
    tradeId: trade.tradeId,
    profile: trade.profile,
    mint: trade.mint,
    openedAt: trade.openedAt,
    entryMarketCapUsd: trade.entryMarketCapUsd,
    originalCloseReason: trade.originalCloseReason,
    originalCloseAt: trade.originalCloseAt,
    compatibleForwardMarks: trade.marks.length,
    mintForwardMarks: trade.mintForwardMarks,
    ...result,
  });
}

const recovered = recoveredTrades.filter((trade) => trade.recovered);
const unresolved = recoveredTrades.filter((trade) => !trade.recovered);
const reasonCounts = (items) => Object.fromEntries([...items.reduce((map, item) => {
  map.set(item.reason, (map.get(item.reason) || 0) + 1);
  return map;
}, new Map())].sort((a, b) => b[1] - a[1]));
const sum = (items, key) => items.reduce((total, item) => total + (finite(item[key]) ?? 0), 0);

const report = {
  generatedAt: new Date().toISOString(),
  mode: "read_only_forward_curve_recovery",
  note: "Original ledgers are not modified. Recovery requires matching mint, opening timestamp, and entry market cap. directCurvePnlSol is the independently captured executable curve quote; comparableNetPnlSol applies the stale-safe-v2 shadow cost model for comparison.",
  inputs: {
    shadowFile: path.resolve(shadowFile),
    forwardFile: path.resolve(forwardFile),
    shadowBytes: fs.statSync(shadowFile).size,
    forwardBytes: fs.statSync(forwardFile).size,
    maxOpenDeltaMs,
    maxEntryMarketCapDeltaPct,
  },
  scan: { forwardLines, malformedForwardLines },
  summary: {
    unpricedTargets: recoveredTrades.length,
    recovered: recovered.length,
    unresolved: unresolved.length,
    coveragePct: recoveredTrades.length ? Number((100 * recovered.length / recoveredTrades.length).toFixed(2)) : 0,
    recoveredReasons: reasonCounts(recovered),
    unresolvedReasons: reasonCounts(unresolved),
    recoveredDirectCurvePnlSol: Number(sum(recovered, "directCurvePnlSol").toFixed(9)),
    recoveredComparableNetPnlSol: Number(sum(recovered, "comparableNetPnlSol").toFixed(9)),
  },
  recoveredTrades: recovered,
  unresolvedTrades: unresolved,
};

const json = JSON.stringify(report, null, 2);
if (outputFile) {
  fs.mkdirSync(path.dirname(outputFile), { recursive: true });
  fs.writeFileSync(outputFile, `${json}\n`, { flag: "wx" });
  console.log(`report=${outputFile}`);
}
console.log(JSON.stringify(report.summary, null, 2));
