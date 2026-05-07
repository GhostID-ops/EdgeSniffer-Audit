import "dotenv/config";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { parseBoostSignal } from "./snipeParser.js";

interface Row {
  timestamp: string;
  unixSeconds: number;
  messageId: number;
  mint: string;
  boostAmount: number;
  totalBoosts: number;
  currentMcUsd: number | "";
  firstBoostMcUsd: number | "";
  athMcUsd: number | "";
  growthPct: number | "";
}

function readEnvNumber(name: string): number {
  const raw = process.env[name];
  if (!raw) throw new Error(`${name} is not set`);
  const n = parseInt(raw, 10);
  if (!Number.isFinite(n)) throw new Error(`${name} is not a number`);
  return n;
}

function readEnvString(name: string): string {
  const raw = process.env[name];
  if (!raw) throw new Error(`${name} is not set`);
  return raw;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const idx = Math.floor(sorted.length * q);
  return sorted[Math.min(idx, sorted.length - 1)];
}

function bucketCount(values: number[], threshold: number): number {
  return values.filter((v) => v >= threshold).length;
}

async function main(): Promise<void> {
  const apiId = readEnvNumber("TELEGRAM_API_ID");
  const apiHash = readEnvString("TELEGRAM_API_HASH");
  const sessionString = readEnvString("TELEGRAM_USER_SESSION");
  const channelRaw = readEnvString("SNIPE_CHANNEL");

  const limit = parseInt(process.env.EXTRACT_LIMIT ?? "0", 10) || 0;
  const sinceDays = parseInt(process.env.EXTRACT_SINCE_DAYS ?? "0", 10) || 0;
  const outputPath =
    process.env.EXTRACT_OUTPUT ??
    path.resolve(process.cwd(), "signals.csv");

  const cutoffSeconds =
    sinceDays > 0
      ? Math.floor(Date.now() / 1000) - sinceDays * 86400
      : 0;

  console.log(`[extract] channel=${channelRaw}`);
  console.log(
    `[extract] limit=${limit || "all"} sinceDays=${sinceDays || "all"} output=${outputPath}`
  );

  const client = new TelegramClient(
    new StringSession(sessionString),
    apiId,
    apiHash,
    { connectionRetries: 5, useWSS: true }
  );
  await client.connect();

  const rows: Row[] = [];
  let scanned = 0;
  let skipped = 0;

  const iterOpts: { limit?: number } = {};
  if (limit > 0) iterOpts.limit = limit;

  for await (const msg of client.iterMessages(channelRaw, iterOpts)) {
    scanned++;

    const date = typeof msg.date === "number" ? msg.date : 0;
    if (cutoffSeconds > 0 && date < cutoffSeconds) {
      console.log(`[extract] reached cutoff at message ${msg.id}, stopping`);
      break;
    }

    const text = msg.message ?? "";
    const parsed = parseBoostSignal(text);
    if (!parsed) {
      skipped++;
      if (scanned % 200 === 0) {
        console.log(`[extract] ${scanned} scanned, ${rows.length} signals so far`);
      }
      continue;
    }

    rows.push({
      timestamp: new Date(date * 1000).toISOString(),
      unixSeconds: date,
      messageId: msg.id,
      mint: parsed.mint,
      boostAmount: parsed.boostAmount,
      totalBoosts: parsed.totalBoosts,
      currentMcUsd: parsed.currentMcUsd ?? "",
      firstBoostMcUsd: parsed.firstBoostMcUsd ?? "",
      athMcUsd: parsed.athMcUsd ?? "",
      growthPct: parsed.growthPct ?? "",
    });

    if (scanned % 200 === 0) {
      console.log(`[extract] ${scanned} scanned, ${rows.length} signals so far`);
    }
  }

  await client.disconnect();

  console.log(
    `[extract] done. scanned=${scanned} parsed=${rows.length} unparsed=${skipped}`
  );

  rows.sort((a, b) => a.unixSeconds - b.unixSeconds);

  const header =
    "timestamp,unix_seconds,message_id,mint,boost_amount,total_boosts,current_mc_usd,first_boost_mc_usd,ath_mc_usd,growth_pct";
  const csvLines = [header];
  for (const r of rows) {
    csvLines.push(
      [
        r.timestamp,
        r.unixSeconds,
        r.messageId,
        r.mint,
        r.boostAmount,
        r.totalBoosts,
        r.currentMcUsd,
        r.firstBoostMcUsd,
        r.athMcUsd,
        r.growthPct,
      ].join(",")
    );
  }
  writeFileSync(outputPath, csvLines.join("\n") + "\n", "utf8");
  console.log(`[extract] wrote ${rows.length} rows → ${outputPath}`);

  if (rows.length === 0) {
    process.exit(0);
  }

  const boostAmounts = rows.map((r) => r.boostAmount).sort((a, b) => a - b);
  const totalBoosts = rows.map((r) => r.totalBoosts).sort((a, b) => a - b);

  console.log("");
  console.log("=== Distribution stats ===");
  console.log(
    `Boost Amount: min=${boostAmounts[0]} p25=${quantile(boostAmounts, 0.25)} p50=${quantile(
      boostAmounts,
      0.5
    )} p75=${quantile(boostAmounts, 0.75)} p90=${quantile(boostAmounts, 0.9)} max=${
      boostAmounts[boostAmounts.length - 1]
    }`
  );
  console.log(
    `Total Boosts: min=${totalBoosts[0]} p25=${quantile(totalBoosts, 0.25)} p50=${quantile(
      totalBoosts,
      0.5
    )} p75=${quantile(totalBoosts, 0.75)} p90=${quantile(totalBoosts, 0.9)} max=${
      totalBoosts[totalBoosts.length - 1]
    }`
  );

  console.log("");
  console.log("Filter coverage (Total Boosts threshold → signals that pass):");
  for (const t of [1, 5, 10, 25, 50, 100, 250, 500, 1000]) {
    const n = bucketCount(totalBoosts, t);
    const pct = ((n / rows.length) * 100).toFixed(1);
    console.log(`  >=${t}: ${n} / ${rows.length} (${pct}%)`);
  }

  console.log("");
  console.log("Filter coverage (Boost Amount threshold → signals that pass):");
  for (const t of [1, 5, 10, 25, 50, 100, 250, 500]) {
    const n = bucketCount(boostAmounts, t);
    const pct = ((n / rows.length) * 100).toFixed(1);
    console.log(`  >=${t}: ${n} / ${rows.length} (${pct}%)`);
  }

  process.exit(0);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
