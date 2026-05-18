import "dotenv/config";
import { appendFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";

declare const WebSocket: any;

type PumpPortalMessage = {
  signature?: string;
  mint?: string;
  traderPublicKey?: string;
  txType?: string;
  initialBuy?: number;
  solAmount?: number;
  vSolInBondingCurve?: number;
  vTokensInBondingCurve?: number;
  marketCapSol?: number;
  name?: string;
  symbol?: string;
  uri?: string;
  pool?: string;
  [key: string]: unknown;
};

const enabled = isTruthy(process.env.EDGE_SCOUT_ENABLED);
const force = process.argv.includes("--force");
const pumpPortalApiKey = process.env.PUMPPORTAL_API_KEY ?? "";
const scoutSourceAccounts = splitCsv(process.env.EDGE_SCOUT_ACCOUNTS ?? process.env.TRACKED_WALLETS ?? "");
const subscribeSourceTrades = isTruthy(process.env.EDGE_SCOUT_SUBSCRIBE_SOURCE_TRADES);
const logFile = process.env.EDGE_SCOUT_LOG_FILE ?? "data/edge-scout.jsonl";
const reconnectMs = Math.max(250, parseInt(process.env.EDGE_SCOUT_RECONNECT_MS ?? "1500", 10));

function isTruthy(value: string | undefined): boolean {
  const normalized = (value ?? "").trim().toLowerCase();
  return normalized === "true" || normalized === "1" || normalized === "yes" || normalized === "on";
}

function splitCsv(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function short(addr: string | undefined): string {
  if (!addr) return "-";
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function pumpPortalUrl(): string {
  const base = "wss://pumpportal.fun/api/data";
  return pumpPortalApiKey ? `${base}?api-key=${encodeURIComponent(pumpPortalApiKey)}` : base;
}

async function writeEvent(kind: string, message: PumpPortalMessage): Promise<void> {
  if (!logFile) return;
  await mkdir(dirname(logFile), { recursive: true });
  await appendFile(
    logFile,
    `${JSON.stringify({
      ts: Date.now(),
      isoTs: new Date().toISOString(),
      kind,
      ...message,
    })}\n`
  );
}

function describeNewToken(message: PumpPortalMessage): string {
  const symbol = typeof message.symbol === "string" && message.symbol ? `$${message.symbol}` : "$?";
  const mint = short(message.mint);
  const creator = short(message.traderPublicKey);
  const buy =
    typeof message.initialBuy === "number"
      ? `${message.initialBuy.toFixed(4)} SOL devbuy`
      : typeof message.solAmount === "number"
        ? `${message.solAmount.toFixed(4)} SOL`
        : "no buy size";
  const mc =
    typeof message.marketCapSol === "number"
      ? ` · mc=${message.marketCapSol.toFixed(1)} SOL`
      : "";
  return `NEW ${symbol} ${mint} creator=${creator} ${buy}${mc}`;
}

function connect(): void {
  const ws = new WebSocket(pumpPortalUrl());
  let settled = false;

  ws.addEventListener("open", () => {
    settled = true;
    console.log("Edge scout connected to PumpPortal.");
    ws.send(JSON.stringify({ method: "subscribeNewToken" }));
    console.log("Edge scout: subscribed to new token stream.");

    if (subscribeSourceTrades && scoutSourceAccounts.length > 0) {
      ws.send(
        JSON.stringify({
          method: "subscribeAccountTrade",
          keys: scoutSourceAccounts,
        })
      );
      console.log(
        `Edge scout: subscribed to ${scoutSourceAccounts.length} source account trade stream(s). ` +
          "This PumpPortal stream is metered."
      );
    }
  });

  ws.addEventListener("message", (event: { data: unknown }) => {
    let message: PumpPortalMessage;
    try {
      message = JSON.parse(String(event.data)) as PumpPortalMessage;
    } catch {
      console.log(`Edge scout raw: ${String(event.data).slice(0, 240)}`);
      return;
    }

    const method = typeof message.txType === "string" ? message.txType : "";
    const isNewToken =
      Boolean(message.mint) &&
      (method === "create" ||
        method === "create_v2" ||
        typeof message.initialBuy === "number" ||
        Boolean(message.uri));

    if (isNewToken) {
      console.log(describeNewToken(message));
      void writeEvent("new-token", message).catch((err) => {
        console.warn(`Edge scout log write failed: ${err instanceof Error ? err.message : String(err)}`);
      });
      return;
    }

    if (message.traderPublicKey && message.mint && message.txType) {
      console.log(
        `TRADE ${message.txType} ${short(message.mint)} trader=${short(message.traderPublicKey)} ` +
          `sol=${typeof message.solAmount === "number" ? message.solAmount.toFixed(4) : "-"}`
      );
      void writeEvent("account-trade", message).catch((err) => {
        console.warn(`Edge scout log write failed: ${err instanceof Error ? err.message : String(err)}`);
      });
    }
  });

  ws.addEventListener("error", (event: unknown) => {
    console.warn(`Edge scout websocket error: ${String(event).slice(0, 240)}`);
  });

  ws.addEventListener("close", () => {
    console.warn(`Edge scout disconnected; reconnecting in ${reconnectMs}ms.`);
    setTimeout(connect, reconnectMs);
  });

  setTimeout(() => {
    if (!settled) console.warn("Edge scout still connecting...");
  }, 5000);
}

if (!enabled && !force) {
  console.log("Edge scout is disabled. Set EDGE_SCOUT_ENABLED=true or run `npm run edge-scout -- --force`.");
  process.exit(0);
}

if (typeof WebSocket === "undefined") {
  console.error("This Node runtime does not expose global WebSocket. Use Node 22+ or add a WebSocket dependency.");
  process.exit(1);
}

console.log("Edge scout starting as a separate process. It does not start or modify the copytrading bot.");
connect();
