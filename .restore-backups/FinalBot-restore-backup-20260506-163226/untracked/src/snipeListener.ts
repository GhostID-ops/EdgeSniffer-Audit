import "dotenv/config";
import { Keypair, LAMPORTS_PER_SOL } from "@solana/web3.js";
import bs58 from "bs58";
import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage, type NewMessageEvent } from "telegram/events/index.js";
import { getPrivateKeyBase58 } from "./config.js";
import { buyTokenWithSol } from "./buyToken.js";
import { getQuote } from "./jupiter.js";
import {
  parseBoostSignal,
  passesFilter,
  type FilterThresholds,
} from "./snipeParser.js";
import { recordSnipeBuy } from "./snipePositions.js";
import { SnipeBot } from "./snipeBot.js";
import { PositionMonitor, parseTrailingLevels } from "./positionMonitor.js";
import {
  registerSnipeKeyboardShortcuts,
  restoreSnipeTerminal,
} from "./snipeKeyboard.js";

const SOL_MINT = "So11111111111111111111111111111111111111112";

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

function readEnvFloat(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const n = parseFloat(raw);
  return Number.isFinite(n) ? n : fallback;
}

function readEnvBool(name: string, fallback: boolean): boolean {
  const raw = (process.env[name] ?? "").toLowerCase();
  if (!raw) return fallback;
  return raw === "true" || raw === "1" || raw === "yes";
}

/** Reads a non-negative integer env var, warning loudly if it's a decimal so it doesn't silently truncate. */
function readIntStrict(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const trimmed = raw.trim();
  if (!/^-?\d+$/.test(trimmed)) {
    const truncated = parseInt(trimmed, 10);
    console.warn(
      `[boot] WARNING: ${name}="${raw}" is not an integer; using ${truncated}. Edit .env to remove decimals.`
    );
    return Number.isFinite(truncated) ? truncated : fallback;
  }
  const n = parseInt(trimmed, 10);
  return Number.isFinite(n) ? n : fallback;
}

const SLIPPAGE_BPS = parseInt(
  process.env.SNIPE_SLIPPAGE_BPS ?? process.env.MIRROR_SLIPPAGE_BPS ?? "1500",
  10
);
const COOLDOWN_MS = parseInt(process.env.SNIPE_COOLDOWN_MS ?? "30000", 10);
const NOTIFY_FILTERED = readEnvBool("SNIPE_NOTIFY_FILTERED", false);

const seenMints = new Map<string, number>();

function isOnCooldown(mint: string): boolean {
  const last = seenMints.get(mint);
  if (last === undefined) return false;
  return Date.now() - last < COOLDOWN_MS;
}

function markSeen(mint: string): void {
  seenMints.set(mint, Date.now());
}

function ts(): string {
  return new Date().toISOString().slice(11, 19);
}

async function handleMessage(
  text: string,
  thresholds: FilterThresholds,
  buyLamports: number,
  dryRun: boolean,
  keypair: Keypair,
  bot: SnipeBot,
  buysPaused: () => boolean
): Promise<void> {
  const signal = parseBoostSignal(text);
  if (!signal) return;

  console.log(
    `[${ts()}] signal mint=${signal.mint} boost=${signal.boostAmount} total=${signal.totalBoosts}`
  );

  if (!passesFilter(signal, thresholds)) {
    console.log(
      `[${ts()}] filtered out (need boost>=${thresholds.minBoostAmount}, total>=${thresholds.minTotalBoosts})`
    );
    if (NOTIFY_FILTERED) {
      void bot.notifyFiltered(signal.mint, signal.boostAmount, signal.totalBoosts);
    }
    return;
  }

  if (isOnCooldown(signal.mint)) {
    console.log(`[${ts()}] cooldown active for ${signal.mint}, skipping`);
    return;
  }

  if (buysPaused()) {
    console.log(`[${ts()}] buys paused; skipping ${signal.mint}`);
    return;
  }

  markSeen(signal.mint);

  if (dryRun) {
    const sol = buyLamports / LAMPORTS_PER_SOL;
    console.log(`[${ts()}] DRY_RUN — paper buy ${signal.mint} for ${sol} SOL`);
    try {
      const quote = await getQuote(SOL_MINT, signal.mint, buyLamports, SLIPPAGE_BPS);
      const outRaw = quote.outAmount ? parseInt(quote.outAmount, 10) : 0;
      if (!Number.isFinite(outRaw) || outRaw <= 0) {
        throw new Error("Quote returned invalid outAmount");
      }
      recordSnipeBuy(signal.mint, buyLamports, outRaw, "sim-buy", true);
      void bot.notifyDryRun(signal.mint, sol, quote.outAmount);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[${ts()}] DRY_RUN quote failed for ${signal.mint}: ${msg}`);
      void bot.notifyBuyFailed(`[SIM] ${signal.mint}`, msg);
    }
    return;
  }

  console.log(
    `[${ts()}] BUY ${signal.mint} for ${buyLamports / LAMPORTS_PER_SOL} SOL slippage=${SLIPPAGE_BPS}bps`
  );
  try {
    const result = await buyTokenWithSol(
      signal.mint,
      buyLamports,
      SLIPPAGE_BPS,
      keypair
    );
    const receivedRaw = result.outAmount
      ? parseInt(result.outAmount, 10)
      : 0;
    recordSnipeBuy(signal.mint, buyLamports, receivedRaw, result.signature, false);
    console.log(
      `[${ts()}] OK sig=${result.signature} status=${result.status} out=${result.outAmount ?? "?"}`
    );
    void bot.notifyBuy({
      mint: signal.mint,
      spentLamports: buyLamports,
      receivedRaw: result.outAmount,
      signature: result.signature,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    console.error(`[${ts()}] BUY FAILED for ${signal.mint}: ${msg}`);
    void bot.notifyBuyFailed(signal.mint, msg);
  }
}

async function main(): Promise<void> {
  const apiId = readEnvNumber("TELEGRAM_API_ID");
  const apiHash = readEnvString("TELEGRAM_API_HASH");
  const sessionString = readEnvString("TELEGRAM_USER_SESSION");
  const channelRaw = readEnvString("SNIPE_CHANNEL");

  const buySol = readEnvFloat("SNIPE_BUY_SOL", 0.05);
  const buyLamports = Math.round(buySol * LAMPORTS_PER_SOL);

  const thresholds: FilterThresholds = {
    minBoostAmount: readIntStrict("MIN_BOOST_AMOUNT", 100),
    minTotalBoosts: readIntStrict("MIN_TOTAL_BOOSTS", 1),
    maxTotalBoosts: readIntStrict("MAX_TOTAL_BOOSTS", 0),
  };
  const dryRun = readEnvBool("SNIPE_DRY_RUN", true);
  let buysPaused = false;
  let shuttingDown = false;

  const keypair = Keypair.fromSecretKey(bs58.decode(getPrivateKeyBase58()));

  console.log(
    `[boot] wallet=${keypair.publicKey.toBase58()} buy=${buySol} SOL slippage=${SLIPPAGE_BPS}bps dryRun=${dryRun}`
  );
  const maxBoostsLabel =
    thresholds.maxTotalBoosts > 0
      ? `<=${thresholds.maxTotalBoosts}`
      : "no-max";
  console.log(
    `[boot] filter: boostAmount>=${thresholds.minBoostAmount} totalBoosts>=${thresholds.minTotalBoosts} (max ${maxBoostsLabel})`
  );

  const client = new TelegramClient(
    new StringSession(sessionString),
    apiId,
    apiHash,
    { connectionRetries: 5, useWSS: true }
  );
  await client.connect();
  console.log(`[boot] telegram connected`);

  const entity = await client.getEntity(channelRaw);
  const targetTitle =
    "title" in entity && typeof entity.title === "string"
      ? entity.title
      : "username" in entity && typeof entity.username === "string"
      ? `@${entity.username}`
      : channelRaw;
  console.log(`[boot] watching: ${targetTitle}`);

  const bot = new SnipeBot({
    keypair,
    slippageBps: SLIPPAGE_BPS,
    statusFn: () => ({
      channelTitle: targetTitle,
      buySol,
      slippageBps: SLIPPAGE_BPS,
      dryRun,
      minBoostAmount: thresholds.minBoostAmount,
      minTotalBoosts: thresholds.minTotalBoosts,
      walletAddress: keypair.publicKey.toBase58(),
    }),
  });
  bot.start();
  if (bot.isEnabled()) {
    console.log(`[boot] snipeBot polling Telegram for commands`);
  }

  const autoExit = readEnvBool("SNIPE_AUTO_EXIT", true);
  let monitor: PositionMonitor | null = null;
  let shutdown: (signal: string) => void = () => undefined;

  shutdown = (signal: string): void => {
    if (shuttingDown) {
      console.log("\nForce exit.");
      process.exit(1);
    }
    shuttingDown = true;
    console.log(`\nReceived ${signal}, shutting down...`);
    monitor?.stop();
    restoreSnipeTerminal();
    void client
      .disconnect()
      .catch((err) => console.error(`[shutdown] telegram disconnect failed: ${err}`))
      .finally(() => process.exit(0));
  };

  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));

  if (autoExit) {
    const tpPct = readEnvFloat("SNIPE_TAKE_PROFIT_PCT", 50);
    const slPct = readEnvFloat("SNIPE_STOP_LOSS_PCT", 30);
    const intervalMs = parseInt(
      process.env.SNIPE_MONITOR_INTERVAL_MS ?? "8000",
      10
    );
    const trailingLevels = parseTrailingLevels(
      process.env.SNIPE_TRAILING_LEVELS
    );
    monitor = new PositionMonitor(bot, {
      takeProfitPct: tpPct,
      stopLossPct: slPct,
      trailingLevels,
      intervalMs,
      slippageBps: SLIPPAGE_BPS,
    });
    monitor.start();
    const trailDesc =
      trailingLevels.length > 0
        ? trailingLevels
            .map((l) => `+${l.triggerPct}%→SL=${l.lockPct >= 0 ? "+" : ""}${l.lockPct}%`)
            .join(", ")
        : "none";
    console.log(
      `[boot] auto-exit ON · TP=+${tpPct}% SL=-${slPct}% trailing=[${trailDesc}] poll=${intervalMs}ms`
    );
  } else {
    console.log(`[boot] auto-exit OFF (manual /sell only)`);
  }

  registerSnipeKeyboardShortcuts({
    bot,
    onQuit: () => shutdown("keyboard"),
    isPaused: () => buysPaused,
    setPaused: (paused) => {
      buysPaused = paused;
    },
    printRuntimeStatus: () => {
      console.log("");
      console.log("Sniper status:");
      console.log(`   Wallet:      ${keypair.publicKey.toBase58()}`);
      console.log(`   Channel:     ${targetTitle}`);
      console.log(`   Buy size:    ${buySol} SOL`);
      console.log(`   Slippage:    ${SLIPPAGE_BPS} bps`);
      console.log(`   Dry run:     ${dryRun ? "yes" : "no"}`);
      console.log(`   New buys:    ${buysPaused ? "PAUSED" : "active"}`);
      console.log(
        `   Filter:      boost>=${thresholds.minBoostAmount}, total>=${thresholds.minTotalBoosts}` +
          (thresholds.maxTotalBoosts > 0
            ? `, total<=${thresholds.maxTotalBoosts}`
            : "")
      );
      console.log(`   Auto-exit:   ${autoExit ? "ON" : "OFF"}`);
      console.log("");
    },
  });

  client.addEventHandler(async (event: NewMessageEvent) => {
    const message = event.message;
    if (!message?.message) return;
    await handleMessage(
      message.message,
      thresholds,
      buyLamports,
      dryRun,
      keypair,
      bot,
      () => buysPaused
    );
  }, new NewMessage({ chats: [channelRaw] }));

  console.log(`[boot] listening for signals…`);
  await new Promise<void>(() => {});
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
