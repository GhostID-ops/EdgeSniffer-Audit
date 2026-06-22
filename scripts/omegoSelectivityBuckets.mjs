// Selectivity bucketing: which of Omego's cycles clear the fee?
// Groups each closed cycle by entry features the BOT CAN SEE AT ENTRY
// (approx entry market cap, Omego's first-buy size, hour of day), and reports
// the follower's NET edge per bucket (cap=1 buy-once, no tips, slip configurable).
//
// A bucket with positive avg net ROI = COPY (clears the ~2% pump.fun fee).
// A bucket with negative avg net ROI = SKIP (you pay fee on a dead cohort).
//
// Usage: node scripts/omegoSelectivityBuckets.mjs [--slip=0.004] [--fee=0.010] [--copy=0.20]

import fs from "node:fs";

const WALLET = "omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq";
const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";
const INPUT = "data/wallet-ledger-cache/wallet-omegoMAe1AMY5MFKQQr3JwXVy8F4eCvmBAfcpo8XAfq-10000.json";
const SOL_USD = (() => {
  try {
    return JSON.parse(fs.readFileSync("data/wallet-ledger-cache/sol-usd.json", "utf8")).price ?? 72.67;
  } catch {
    return 72.67;
  }
})();
const PUMP_SUPPLY = 1e9; // pump.fun fixed supply
const OUT_MD = "data/omego-selectivity-buckets.md";

const arg = (n, d) => {
  const h = process.argv.find((a) => a.startsWith(`--${n}=`));
  return h ? h.slice(n.length + 3) : d;
};
const COPY = Number(arg("copy", "0.20"));
const SLIP = Number(arg("slip", "0.004"));
const FEE = Number(arg("fee", "0.010"));
const TIP_LAMPORTS = Math.round(Number(arg("tip", "0")) * 1e9);

const sol = (l) => l / 1e9;
const fmtPct = (v) => (v == null || !Number.isFinite(v) ? "-" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`);
const fmtSol = (v) => `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;

function extractSwap(tx) {
  let nativeNet = 0;
  const tokenNet = new Map();
  const decByMint = new Map();
  for (const a of tx.accountData ?? []) {
    if (a.account === WALLET) nativeNet += a.nativeBalanceChange ?? 0;
    for (const c of a.tokenBalanceChanges ?? []) {
      if (c.userAccount !== WALLET) continue;
      const m = c.mint;
      if (!m || m === WRAPPED_SOL_MINT) continue;
      const raw = Number(c.rawTokenAmount?.tokenAmount ?? "0");
      if (!Number.isFinite(raw) || raw === 0) continue;
      tokenNet.set(m, (tokenNet.get(m) ?? 0) + raw);
      decByMint.set(m, c.rawTokenAmount?.decimals ?? 6);
    }
  }
  let mint = null,
    tok = 0;
  for (const [m, r] of tokenNet) if (Math.abs(r) > Math.abs(tok)) ((mint = m), (tok = r));
  if (!mint || tok === 0 || nativeNet === 0) return null;
  const base = { signature: tx.signature, timestamp: tx.timestamp ?? 0, mint, decimals: decByMint.get(mint) ?? 6 };
  if (nativeNet < 0 && tok > 0) return { ...base, side: "buy", solLamports: Math.abs(nativeNet), tokenRaw: Math.abs(tok) };
  if (nativeNet > 0 && tok < 0) return { ...base, side: "sell", solLamports: nativeNet, tokenRaw: Math.abs(tok) };
  return null;
}

function buildCampaigns(swaps) {
  const open = new Map();
  const out = [];
  const sorted = [...swaps].sort((a, b) => a.timestamp - b.timestamp || a.signature.localeCompare(b.signature));
  for (const s of sorted) {
    let c = open.get(s.mint);
    if (!c) {
      if (s.side !== "buy") continue;
      c = { mint: s.mint, events: [], boughtRaw: 0, soldRaw: 0, firstBuy: s };
      open.set(s.mint, c);
      out.push(c);
    }
    c.events.push(s);
    if (s.side === "buy") c.boughtRaw += s.tokenRaw;
    else {
      c.soldRaw += s.tokenRaw;
      if (c.boughtRaw > 0 && c.soldRaw >= c.boughtRaw * 0.995) open.delete(s.mint);
    }
  }
  return out;
}

// follower net for cap=1 (buy-once), proportional mirror sells
function followerNet(c) {
  let omegoTok = 0,
    ourTok = 0,
    grossCost = 0,
    grossProceeds = 0,
    legs = 0,
    buysDone = 0;
  for (const ev of c.events) {
    if (ev.side === "buy") {
      omegoTok += ev.tokenRaw;
      if (buysDone < 1) {
        grossCost += ev.solLamports * COPY;
        ourTok += ev.tokenRaw * COPY;
        buysDone++;
        legs++;
      }
    } else {
      if (omegoTok <= 0) continue;
      const frac = Math.min(1, ev.tokenRaw / omegoTok);
      omegoTok -= ev.tokenRaw;
      if (ourTok > 0) {
        grossProceeds += ourTok * frac * (ev.solLamports / ev.tokenRaw);
        ourTok -= ourTok * frac;
        legs++;
      }
    }
  }
  const turnover = grossCost + grossProceeds;
  const netLamports = grossProceeds - grossCost - turnover * FEE - turnover * SLIP - TIP_LAMPORTS * legs;
  return { grossCost, netLamports, grossRoi: grossCost > 0 ? ((grossProceeds - grossCost) / grossCost) * 100 : null, netRoi: grossCost > 0 ? (netLamports / grossCost) * 100 : null };
}

function bucketize(rows, keyFn, order) {
  const map = new Map();
  for (const r of rows) {
    const k = keyFn(r);
    if (k == null) continue;
    if (!map.has(k)) map.set(k, []);
    map.get(k).push(r);
  }
  const keys = order ?? [...map.keys()].sort();
  return keys
    .filter((k) => map.has(k))
    .map((k) => {
      const xs = map.get(k);
      const net = xs.reduce((s, x) => s + x.netLamports, 0);
      const deployed = xs.reduce((s, x) => s + x.grossCost, 0);
      const wins = xs.filter((x) => x.netLamports > 0).length;
      const netRois = xs.map((x) => x.netRoi).filter(Number.isFinite);
      const grossRois = xs.map((x) => x.grossRoi).filter(Number.isFinite);
      return {
        bucket: k,
        cycles: xs.length,
        deployedSol: sol(deployed),
        netSol: sol(net),
        avgGrossRoi: grossRois.reduce((s, v) => s + v, 0) / (grossRois.length || 1),
        avgNetRoi: netRois.reduce((s, v) => s + v, 0) / (netRois.length || 1),
        winPct: (wins / xs.length) * 100,
        verdict: net > 0 ? "✅ COPY" : "❌ SKIP",
      };
    });
}

function table(title, buckets) {
  const head = `### ${title}\n\n| Bucket | Cycles | Deployed | Avg gross ROI | Avg NET ROI | Win % | Net SOL | Verdict |\n|---|---:|---:|---:|---:|---:|---:|:--|`;
  const body = buckets
    .map((b) => `| ${b.bucket} | ${b.cycles} | ${b.deployedSol.toFixed(1)} | ${fmtPct(b.avgGrossRoi)} | ${fmtPct(b.avgNetRoi)} | ${b.winPct.toFixed(0)}% | ${fmtSol(b.netSol)} | ${b.verdict} |`)
    .join("\n");
  return `${head}\n${body}\n`;
}

function main() {
  const rows0 = JSON.parse(fs.readFileSync(INPUT, "utf8"));
  const seen = new Set();
  const swaps = [];
  for (const tx of rows0) {
    if (!tx?.signature || seen.has(tx.signature)) continue;
    seen.add(tx.signature);
    const s = extractSwap(tx);
    if (s) swaps.push(s);
  }
  const campaigns = buildCampaigns(swaps);

  const rows = [];
  for (const c of campaigns) {
    const closed = c.boughtRaw > 0 && c.soldRaw >= c.boughtRaw * 0.995;
    if (!closed) continue;
    const fb = c.firstBuy;
    const wholePrice = fb.solLamports / 1e9 / (fb.tokenRaw / 10 ** fb.decimals); // SOL per whole token
    const mcUsd = wholePrice * PUMP_SUPPLY * SOL_USD;
    const sizeSol = fb.solLamports / 1e9;
    const hour = new Date(fb.timestamp * 1000).getUTCHours();
    const fn = followerNet(c);
    if (fn.grossCost <= 0) continue;
    rows.push({ ...fn, mcUsd, sizeSol, hour });
  }

  const mcBucket = (r) => {
    const k = r.mcUsd;
    if (k < 8000) return "1) <$8k";
    if (k < 15000) return "2) $8-15k";
    if (k < 25000) return "3) $15-25k";
    if (k < 40000) return "4) $25-40k";
    if (k < 70000) return "5) $40-70k";
    return "6) >$70k";
  };
  const sizeBucket = (r) => {
    const k = r.sizeSol;
    if (k < 0.4) return "1) <0.4 SOL";
    if (k < 0.6) return "2) 0.4-0.6";
    if (k < 0.9) return "3) 0.6-0.9";
    if (k < 1.3) return "4) 0.9-1.3";
    return "5) >1.3 SOL";
  };
  const hourBucket = (r) => {
    const h = r.hour;
    if (h < 6) return "1) 00-06 UTC";
    if (h < 12) return "2) 06-12 UTC";
    if (h < 18) return "3) 12-18 UTC";
    return "4) 18-24 UTC";
  };

  const order = (n) => Array.from({ length: n }, (_, i) => i);
  const mc = bucketize(rows, mcBucket, ["1) <$8k", "2) $8-15k", "3) $15-25k", "4) $25-40k", "5) $40-70k", "6) >$70k"]);
  const size = bucketize(rows, sizeBucket, ["1) <0.4 SOL", "2) 0.4-0.6", "3) 0.6-0.9", "4) 0.9-1.3", "5) >1.3 SOL"]);
  const hour = bucketize(rows, hourBucket, ["1) 00-06 UTC", "2) 06-12 UTC", "3) 12-18 UTC", "4) 18-24 UTC"]);

  const total = rows.reduce((s, r) => s + r.netLamports, 0);
  const md = `# Omego Selectivity Buckets — which cycles clear the fee

Source: ${INPUT}
Closed cycles: ${rows.length} · SOL=$${SOL_USD} · copy=${(COPY * 100).toFixed(0)}% · drag = fee ${(FEE * 100).toFixed(1)}%/leg + slip ${(SLIP * 100).toFixed(1)}%/leg + tip ${sol(TIP_LAMPORTS).toFixed(4)}/leg (no tips)
Baseline (copy ALL closed cycles, buy-once): **${fmtSol(sol(total))} SOL net**

Net ROI is AFTER drag. A bucket clears the pump.fun fee when **Avg NET ROI > 0**. "MC" = entry market cap estimate (entry price x 1B supply x SOL price).

${table("By entry market cap (most actionable — bot has entry price / virtSol)", mc)}
${table("By Omego's first-buy size (bot sees source amount)", size)}
${table("By hour of day (UTC)", hour)}
## How to turn this into bot filters
- Keep only **✅ COPY** buckets; the **❌ SKIP** buckets are where you pay fee on a net-losing cohort.
- Entry-MC gate maps directly to a virtSol / entry-price filter in the buy path.
- Buy-size gate maps to a min/max source-amount filter.
- Re-run with your real --slip to harden the cutoffs.
`;
  fs.writeFileSync(OUT_MD, md);
  console.log(md);
}

main();
