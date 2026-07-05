#!/usr/bin/env node
// Same-slot latency analyzer for CyA (and any tracked-wallet) copy trades.
//
// Joins execution-telemetry.jsonl `source-detected` events (which carry the
// tracked wallet's on-chain slot, `sourceSlot`) with `stage:confirm` events
// (which carry OUR `landingSlot`) on `sourceSignature`. The slot delta tells us
// how many slots behind the source we landed: 0 = same slot, 1 = one behind, etc.
// No RPC required — everything is already in the telemetry.
//
// Usage: node scripts/analyzeSameSlot.mjs <path-to-execution-telemetry.jsonl> [--side buy|sell]
//
import { readFileSync } from "node:fs";

const path = process.argv[2];
if (!path) {
  console.error("usage: node scripts/analyzeSameSlot.mjs <execution-telemetry.jsonl> [--side buy|sell]");
  process.exit(1);
}
const sideArg = (() => {
  const i = process.argv.indexOf("--side");
  return i >= 0 ? process.argv[i + 1] : "buy";
})();
const sinceArg = (() => {
  const i = process.argv.indexOf("--since");
  return i >= 0 ? process.argv[i + 1] : null; // ISO date prefix, e.g. 2026-07-05
})();

const getSlot = (e) => e.sourceSlot ?? e.details?.sourceSlot ?? null;
const getLanding = (e) => e.details?.landingSlot ?? e.landingSlot ?? null;
const normalizePath = (p) => {
  if (!p) return null;
  // Strip a trailing ":<base58 signature>" that some records append to senderPath,
  // while preserving meaningful compound labels like "jito:race" / "race:jito+helius".
  return String(p)
    .split(":")
    .filter((seg) => !/^[1-9A-HJ-NP-Za-km-z]{32,}$/.test(seg))
    .join(":") || null;
};
const getPath = (e) => normalizePath(e.senderPath ?? e.details?.senderPath ?? e.details?.path ?? null);

// sourceSignature -> { sourceSlot }
const sourceBySig = new Map();
// sourceSignature -> landed path (from broadcast events)
const pathBySig = new Map();
// collected confirmed fills
const confirms = [];
let lines = 0, bad = 0;

for (const raw of readFileSync(path, "utf8").split("\n")) {
  const line = raw.trim();
  if (!line) continue;
  lines++;
  let e;
  try { e = JSON.parse(line); } catch { bad++; continue; }
  if (e.side && sideArg && e.side !== sideArg) {
    // keep source-detected/broadcast regardless of side mismatch only if side missing
  }
  if (e.kind === "source-detected") {
    const slot = getSlot(e);
    if (slot != null && e.sourceSignature) sourceBySig.set(e.sourceSignature, slot);
  } else if (e.kind === "broadcast" || e.kind === "stage:broadcast") {
    const p = getPath(e);
    if (p && e.sourceSignature) pathBySig.set(e.sourceSignature, p);
  } else if (e.kind === "stage:confirm" && e.success && getLanding(e) != null) {
    if (sideArg && e.side && e.side !== sideArg) continue;
    if (sinceArg && !(e.ts ?? "").startsWith(sinceArg) && (e.ts ?? "") < sinceArg) continue;
    confirms.push(e);
  }
}

const pairs = [];
for (const c of confirms) {
  const sourceSlot = c.sourceSignature ? sourceBySig.get(c.sourceSignature) : null;
  const landingSlot = getLanding(c);
  if (sourceSlot == null || landingSlot == null) continue;
  pairs.push({
    mint: c.mint,
    route: c.route ?? "?",
    sourceSlot,
    landingSlot,
    delta: landingSlot - sourceSlot,
    landedPath: (c.sourceSignature && pathBySig.get(c.sourceSignature)) ?? getPath(c) ?? "?",
    ts: c.ts,
  });
}

if (pairs.length === 0) {
  console.log(`No source↔landing slot pairs found in ${path}.`);
  console.log(`(lines=${lines}, source-detected w/slot=${sourceBySig.size}, confirms w/landingSlot=${confirms.length})`);
  process.exit(0);
}

const deltas = pairs.map((p) => p.delta).sort((a, b) => a - b);
const median = deltas[Math.floor(deltas.length / 2)];
const same = deltas.filter((d) => d === 0).length;
const withinOne = deltas.filter((d) => d <= 1 && d >= 0).length;
const negatives = deltas.filter((d) => d < 0).length;
const pct = (n) => `${((100 * n) / pairs.length).toFixed(1)}%`;

const hist = new Map();
for (const d of deltas) {
  const bucket = d < 0 ? "<0 (data)" : d >= 5 ? "5+" : String(d);
  hist.set(bucket, (hist.get(bucket) ?? 0) + 1);
}

console.log(`\n=== Same-slot analysis (${sideArg}) — ${path} ===`);
console.log(`Matched fills:        ${pairs.length}`);
console.log(`Same slot (Δ=0):      ${same}  (${pct(same)})`);
console.log(`Within +1 slot:       ${withinOne}  (${pct(withinOne)})`);
console.log(`Median slot delta:    ${median}`);
console.log(`Mean slot delta:      ${(deltas.reduce((a, b) => a + b, 0) / deltas.length).toFixed(2)}`);
if (negatives) console.log(`Negative deltas:      ${negatives} (landed before source — telemetry/slot-source anomaly)`);

console.log(`\nSlot-delta distribution:`);
for (const [bucket, n] of [...hist.entries()].sort((a, b) => {
  const na = a[0] === "<0 (data)" ? -1 : a[0] === "5+" ? 99 : Number(a[0]);
  const nb = b[0] === "<0 (data)" ? -1 : b[0] === "5+" ? 99 : Number(b[0]);
  return na - nb;
})) {
  const bar = "█".repeat(Math.round((40 * n) / pairs.length));
  console.log(`  Δ=${bucket.padEnd(9)} ${String(n).padStart(4)}  ${bar}`);
}

// By landed sender path
const byPath = new Map();
for (const p of pairs) {
  const k = p.landedPath;
  const cur = byPath.get(k) ?? { n: 0, same: 0, sum: 0 };
  cur.n++; cur.sum += p.delta; if (p.delta === 0) cur.same++;
  byPath.set(k, cur);
}
console.log(`\nBy landed sender path:`);
for (const [k, v] of [...byPath.entries()].sort((a, b) => b[1].n - a[1].n)) {
  console.log(`  ${String(k).padEnd(22)} n=${String(v.n).padStart(4)}  same-slot=${((100 * v.same) / v.n).toFixed(0).padStart(3)}%  meanΔ=${(v.sum / v.n).toFixed(2)}`);
}

// By route
const byRoute = new Map();
for (const p of pairs) {
  const cur = byRoute.get(p.route) ?? { n: 0, same: 0, sum: 0 };
  cur.n++; cur.sum += p.delta; if (p.delta === 0) cur.same++;
  byRoute.set(p.route, cur);
}
console.log(`\nBy route:`);
for (const [k, v] of [...byRoute.entries()].sort((a, b) => b[1].n - a[1].n)) {
  console.log(`  ${String(k).padEnd(28)} n=${String(v.n).padStart(4)}  same-slot=${((100 * v.same) / v.n).toFixed(0).padStart(3)}%  meanΔ=${(v.sum / v.n).toFixed(2)}`);
}
console.log("");
