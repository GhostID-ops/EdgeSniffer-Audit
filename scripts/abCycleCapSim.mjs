// A/B simulation: rank "max buys per cycle" copy strategies on Omego's cached tape.
//
// A "campaign" = one holding cycle: opens on first buy, closes when fully sold (>=99.5%
// of bought tokens). The next buy starts a fresh cycle — i.e. the buy counter resets when
// Omego sells out. Capping buys per campaign therefore models exactly:
//   "max N buys per token; if Omego sells (closes the cycle), it resets."
//
// We model OUR position as a proportional-mirror follower (mirror his sells by fraction),
// then deduct a follower-drag haircut so stacking buys is correctly penalized for fees:
//   - protocol fee (FEE) per leg, on traded notional
//   - slippage (SLIP) per leg vs his fill (we're a beat late on both sides)
//   - fixed tip (TIP_SOL) per executed leg
//
// grossProceeds - grossCost            = "trade-basis" PnL (what the bot UI shows)
// minus fee + slippage + tip drags     = wallet-basis PnL (what actually hits the wallet)
//
// Usage:
//   node scripts/abCycleCapSim.mjs [--copy=0.20] [--slip=0.006] [--fee=0.010] [--tip=0.0025]
//   node scripts/abCycleCapSim.mjs --caps=1,2,3,4,inf

import fs from "node:fs";

const WALLET = "omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

const INPUTS = [
  "data/wallet-ledger-cache/wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-10000.json",
  "data/wallet-ledger-cache/wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-3000.json",
];
const OUT_JSON = "data/omego-ab-cycle-cap-sim.json";
const OUT_MD = "data/omego-ab-cycle-cap-sim.md";

function arg(name, fallback) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
}
const COPY = Number(arg("copy", "0.20"));
const SLIP = Number(arg("slip", "0.006"));
const FEE = Number(arg("fee", "0.010"));
const TIP_LAMPORTS = Math.round(Number(arg("tip", "0.0025")) * 1e9);
const CAPS = String(arg("caps", "1,2,3,4,inf"))
  .split(",")
  .map((c) => (c.trim().toLowerCase() === "inf" ? Infinity : Number(c.trim())));

const sol = (lamports) => lamports / 1e9;
const fmtSol = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(4)}`;
const fmtPct = (v) => (v == null || !Number.isFinite(v) ? "-" : `${v.toFixed(1)}%`);
const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};

// --- identical swap extraction to analyzeOmegoDayWeek.mjs ---
function extractSwap(tx) {
  let nativeNet = 0;
  const tokenNet = new Map();
  const decimalsByMint = new Map();
  for (const account of tx.accountData ?? []) {
    if (account.account === WALLET) nativeNet += account.nativeBalanceChange ?? 0;
    for (const change of account.tokenBalanceChanges ?? []) {
      if (change.userAccount !== WALLET) continue;
      const mint = change.mint;
      if (!mint || mint === WRAPPED_SOL_MINT) continue;
      const raw = Number(change.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(mint, (tokenNet.get(mint) ?? 0) + raw);
      decimalsByMint.set(mint, change.rawTokenAmount?.decimals ?? 0);
    }
  }
  let mint = null;
  let tokenRaw = 0;
  for (const [candidate, raw] of tokenNet) {
    if (Math.abs(raw) > Math.abs(tokenRaw)) {
      mint = candidate;
      tokenRaw = raw;
    }
  }
  if (!mint || tokenRaw === 0 || nativeNet === 0) return null;
  if (nativeNet < 0 && tokenRaw > 0)
    return { signature: tx.signature, timestamp: tx.timestamp ?? 0, side: "buy", mint, solLamports: Math.abs(nativeNet), tokenRaw: Math.abs(tokenRaw) };
  if (nativeNet > 0 && tokenRaw < 0)
    return { signature: tx.signature, timestamp: tx.timestamp ?? 0, side: "sell", mint, solLamports: nativeNet, tokenRaw: Math.abs(tokenRaw) };
  return null;
}

// Build campaigns but keep events in chronological order so we can replay fractions.
function buildCampaigns(swaps) {
  const openByMint = new Map();
  const campaigns = [];
  const sorted = [...swaps].sort((a, b) => a.timestamp - b.timestamp || a.signature.localeCompare(b.signature));
  for (const swap of sorted) {
    let c = openByMint.get(swap.mint);
    if (!c) {
      if (swap.side !== "buy") continue;
      c = { mint: swap.mint, events: [], boughtRaw: 0, soldRaw: 0 };
      openByMint.set(swap.mint, c);
      campaigns.push(c);
    }
    c.events.push(swap);
    if (swap.side === "buy") c.boughtRaw += swap.tokenRaw;
    else {
      c.soldRaw += swap.tokenRaw;
      if (c.boughtRaw > 0 && c.soldRaw >= c.boughtRaw * 0.995) openByMint.delete(swap.mint);
    }
  }
  return campaigns;
}

// Simulate a proportional-mirror follower with a per-cycle buy cap.
function simulateCampaign(c, cap) {
  let omegoTok = 0;
  let ourTok = 0;
  let grossCost = 0; // lamports we paid at his prices (no drag)
  let grossProceeds = 0; // lamports we received at his prices (no drag)
  let buysExec = 0;
  let sellsExec = 0;
  let buysDone = 0;

  for (const ev of c.events) {
    if (ev.side === "buy") {
      omegoTok += ev.tokenRaw;
      if (buysDone < cap) {
        grossCost += ev.solLamports * COPY;
        ourTok += ev.tokenRaw * COPY;
        buysDone++;
        buysExec++;
      }
    } else {
      if (omegoTok <= 0) continue;
      const frac = Math.min(1, ev.tokenRaw / omegoTok);
      omegoTok -= ev.tokenRaw;
      if (ourTok > 0) {
        const price = ev.solLamports / ev.tokenRaw; // his realized lamports/token
        const ourSell = ourTok * frac;
        grossProceeds += ourSell * price;
        ourTok -= ourSell;
        sellsExec++;
      }
    }
  }

  const closed = c.boughtRaw > 0 && c.soldRaw >= c.boughtRaw * 0.995;
  const turnover = grossCost + grossProceeds;
  const feeDrag = turnover * FEE;
  const slipDrag = turnover * SLIP;
  const tipDrag = TIP_LAMPORTS * (buysExec + sellsExec);
  const tradeBasis = grossProceeds - grossCost;
  const netLamports = tradeBasis - feeDrag - slipDrag - tipDrag;
  return {
    closed,
    deployed: grossCost,
    tradeBasisLamports: tradeBasis,
    netLamports,
    feeDrag,
    slipDrag,
    tipDrag,
    buysExec,
    sellsExec,
    roiPct: grossCost > 0 ? (netLamports / grossCost) * 100 : null,
  };
}

function runStrategy(campaigns, cap) {
  let net = 0,
    tradeBasis = 0,
    deployed = 0,
    fee = 0,
    slip = 0,
    tip = 0,
    buys = 0,
    sells = 0,
    closedCount = 0,
    openCount = 0,
    wins = 0;
  const rois = [];
  for (const c of campaigns) {
    const r = simulateCampaign(c, cap);
    if (r.buysExec === 0) continue; // we never entered this cycle
    if (!r.closed) {
      openCount++;
      continue; // realized-only: skip cycles Omego never fully closed in the cache
    }
    closedCount++;
    net += r.netLamports;
    tradeBasis += r.tradeBasisLamports;
    deployed += r.deployed;
    fee += r.feeDrag;
    slip += r.slipDrag;
    tip += r.tipDrag;
    buys += r.buysExec;
    sells += r.sellsExec;
    if (r.netLamports > 0) wins++;
    if (r.roiPct != null) rois.push(r.roiPct);
  }
  return {
    cap: cap === Infinity ? "inf" : cap,
    closedCycles: closedCount,
    openSkipped: openCount,
    buys,
    sells,
    deployedSol: sol(deployed),
    tradeBasisSol: sol(tradeBasis),
    netSol: sol(net),
    feeDragSol: sol(fee),
    slipDragSol: sol(slip),
    tipDragSol: sol(tip),
    totalDragSol: sol(fee + slip + tip),
    winPct: closedCount ? (wins / closedCount) * 100 : null,
    medRoiPct: median(rois),
    netPerCycleSol: closedCount ? sol(net) / closedCount : null,
  };
}

function main() {
  const seen = new Set();
  const swaps = [];
  let loadedFile = null;
  for (const file of INPUTS) {
    if (!fs.existsSync(file)) continue;
    loadedFile = file;
    const rows = JSON.parse(fs.readFileSync(file, "utf8"));
    for (const tx of rows) {
      if (!tx?.signature || seen.has(tx.signature)) continue;
      seen.add(tx.signature);
      const s = extractSwap(tx);
      if (s) swaps.push(s);
    }
    break; // first existing input wins (10k preferred)
  }
  if (!swaps.length) {
    console.error("No swaps extracted. Checked:", INPUTS.join(", "));
    process.exit(1);
  }
  const campaigns = buildCampaigns(swaps);
  const rows = CAPS.map((cap) => runStrategy(campaigns, cap));

  const header =
    "| Strategy (max buys/cycle) | Cycles | Buys | Sells | Deployed SOL | Trade-basis PnL | Net PnL (after drag) | Fee | Slip | Tip | Total drag | Win % | Med ROI | Net/cycle |";
  const sep = "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|";
  const body = rows
    .map(
      (r) =>
        `| ${r.cap === "inf" ? "Mirror-all (∞)" : `Cap ${r.cap}`} | ${r.closedCycles} | ${r.buys} | ${r.sells} | ${r.deployedSol.toFixed(2)} | ${fmtSol(r.tradeBasisSol)} | ${fmtSol(r.netSol)} | ${r.feeDragSol.toFixed(3)} | ${r.slipDragSol.toFixed(3)} | ${r.tipDragSol.toFixed(3)} | ${r.totalDragSol.toFixed(3)} | ${fmtPct(r.winPct)} | ${fmtPct(r.medRoiPct)} | ${r.netPerCycleSol == null ? "-" : fmtSol(r.netPerCycleSol)} |`
    )
    .join("\n");

  const md = `# Omego A/B — Max Buys Per Cycle (follower simulation)

Generated: ${new Date().toISOString()}
Source tape: ${loadedFile}
Extracted swaps: ${swaps.length} · Campaigns (cycles): ${campaigns.length}

Follower model: proportional mirror-sell, copy=${(COPY * 100).toFixed(0)}%, drag = fee ${(FEE * 100).toFixed(1)}%/leg + slippage ${(SLIP * 100).toFixed(1)}%/leg + tip ${sol(TIP_LAMPORTS).toFixed(4)} SOL/leg. Realized-only (cycles Omego left open in the cache are skipped).

${header}
${sep}
${body}

## How to read
- **Trade-basis PnL** = what the bot UI would show (his price replay). **Net PnL** = after your real fee/slip/tip drag — the wallet number.
- A higher cap captures more of his scalps (higher trade-basis) but stacks more drag. The winner is the cap where net, not trade-basis, peaks.
- "Mirror-all (∞)" is today's behavior. "Cap 1" is buy-once.
- Drag assumptions are tunable: re-run with --slip / --fee / --tip from your real fills to harden the result.
`;

  fs.writeFileSync(OUT_JSON, JSON.stringify({ params: { COPY, SLIP, FEE, TIP_LAMPORTS, loadedFile, swaps: swaps.length, campaigns: campaigns.length }, rows }, null, 2));
  fs.writeFileSync(OUT_MD, md);
  console.log(md);
  console.error(`\nWrote ${OUT_MD} and ${OUT_JSON}`);
}

main();
