import { createServer, IncomingMessage, ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import {
  startBot,
  stopBot,
  getBotState,
  sellAllPositions,
  sellPositionByIndex,
  sellPositionPercentByIndex,
  closeAllOpenPositionsSimulated,
  resetTradingState,
  setBuysPaused,
} from "./botService.js";
import { getLogs } from "./logBuffer.js";
import { getFriendBotSettings } from "./config.js";
import { normalizeFriendSettings, persistFriendSettings } from "./settingsStore.js";
import { getTokenPairInfo, WRAPPED_SOL_MINT } from "./tokenRegistry.js";
import { getQuote } from "./jupiter.js";
import { getPriceSampleMs, getPriceSeries, startPriceSampler } from "./priceSampler.js";

const PORT = parseInt(process.env.WEB_PORT ?? "3000", 10);
// Interface to bind. Default 127.0.0.1 (localhost only). Set WEB_HOST to your
// Tailscale IP (e.g. 100.x.y.z) to reach it from your tailnet devices WITHOUT
// exposing it publicly. Avoid 0.0.0.0 unless the port is firewalled — the UI has
// no auth, so a public bind lets anyone drain the wallet.
const HOST = (process.env.WEB_HOST ?? "127.0.0.1").trim() || "127.0.0.1";
const TOKEN_LIVE_CACHE_MS = Math.max(
  5_000,
  parseInt(process.env.UI_TOKEN_LIVE_CACHE_MS ?? "20000", 10)
);

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = resolve(here, "..", "public");

type TokenLiveBody = {
  pair: Awaited<ReturnType<typeof getTokenPairInfo>>;
  quote: {
    outSolLamports: number;
    unrealizedPnlLamports: number | null;
    unrealizedPnlPct: number | null;
    priceImpactPct: number | null;
  } | null;
  checkedAt: number;
  cached?: boolean;
};

const tokenLiveCache = new Map<string, TokenLiveBody>();

type StartServerOptions = {
  optional?: boolean;
};

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(text),
  });
  res.end(text);
}

function sendError(res: ServerResponse, status: number, message: string): void {
  sendJson(res, status, { error: message });
}

function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  return new Promise((resolveBody, reject) => {
    let raw = "";
    req.setEncoding("utf8");
    req.on("data", (chunk) => {
      raw += chunk;
      if (raw.length > 64_000) {
        reject(new Error("Request body too large"));
        req.destroy();
      }
    });
    req.on("end", () => {
      if (!raw.trim()) return resolveBody({});
      try {
        const parsed = JSON.parse(raw);
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          reject(new Error("Expected a JSON object"));
          return;
        }
        resolveBody(parsed as Record<string, unknown>);
      } catch {
        reject(new Error("Invalid JSON body"));
      }
    });
    req.on("error", reject);
  });
}

function serveIndex(res: ServerResponse): void {
  try {
    const html = readFileSync(join(publicDir, "index.html"));
    res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Length": html.length,
    });
    res.end(html);
  } catch {
    res.writeHead(500);
    res.end("index.html not found");
  }
}

function servePublicFile(res: ServerResponse, filename: string, contentType: string): void {
  try {
    const body = readFileSync(join(publicDir, filename));
    res.writeHead(200, {
      "Content-Type": contentType,
      "Content-Length": body.length,
    });
    res.end(body);
  } catch {
    res.writeHead(404);
    res.end("Not found");
  }
}

async function handle(
  req: IncomingMessage,
  res: ServerResponse
): Promise<void> {
  const url = req.url ?? "/";

  if (req.method === "GET" && (url === "/" || url === "/index.html")) {
    return serveIndex(res);
  }

  if (req.method === "GET" && (url === "/dex" || url === "/dex.html")) {
    return servePublicFile(res, "dex.html", "text/html; charset=utf-8");
  }

  if (req.method === "GET" && url === "/favicon.svg") {
    return servePublicFile(res, "favicon.svg", "image/svg+xml; charset=utf-8");
  }

  if (req.method === "GET" && url === "/manifest.webmanifest") {
    return servePublicFile(res, "manifest.webmanifest", "application/manifest+json; charset=utf-8");
  }

  if (req.method === "GET" && url === "/api/state") {
    return sendJson(res, 200, getBotState());
  }

  if (req.method === "GET" && url.startsWith("/api/price-series")) {
    const u = new URL(url, "http://localhost");
    const mint = (u.searchParams.get("mint") ?? "").trim();
    if (!mint) return sendError(res, 400, "Missing mint");
    return sendJson(res, 200, {
      mint,
      sampleMs: getPriceSampleMs(),
      points: getPriceSeries(mint),
    });
  }

  if (req.method === "POST" && url === "/api/actions/pause-buys") {
    setBuysPaused(true);
    return sendJson(res, 200, { ok: true, state: getBotState() });
  }

  if (req.method === "POST" && url === "/api/actions/resume-buys") {
    setBuysPaused(false);
    return sendJson(res, 200, { ok: true, state: getBotState() });
  }

  if (req.method === "GET" && url === "/api/settings") {
    return sendJson(res, 200, getFriendBotSettings());
  }

  if (req.method === "GET" && url.startsWith("/api/token-pair")) {
    const u = new URL(url, "http://localhost");
    const mint = (u.searchParams.get("mint") ?? "").trim();
    if (!mint) return sendError(res, 400, "Missing mint");
    return sendJson(res, 200, { pair: await getTokenPairInfo(mint) });
  }

  if (req.method === "GET" && url.startsWith("/api/token-live")) {
    const u = new URL(url, "http://localhost");
    const mint = (u.searchParams.get("mint") ?? "").trim();
    const amountRaw = parseInt(u.searchParams.get("amountRaw") ?? "0", 10);
    const costBasisLamports = parseFloat(u.searchParams.get("costBasisLamports") ?? "0");
    if (!mint) return sendError(res, 400, "Missing mint");
    const cacheKey = `${mint}:${amountRaw}:${costBasisLamports}`;
    const cached = tokenLiveCache.get(cacheKey);
    if (cached && Date.now() - cached.checkedAt < TOKEN_LIVE_CACHE_MS) {
      return sendJson(res, 200, { ...cached, cached: true });
    }
    const pair = await getTokenPairInfo(mint);
    let quote: {
      outSolLamports: number;
      unrealizedPnlLamports: number | null;
      unrealizedPnlPct: number | null;
      priceImpactPct: number | null;
    } | null = null;
    if (Number.isFinite(amountRaw) && amountRaw > 0) {
      try {
        const q = await getQuote(mint, WRAPPED_SOL_MINT, amountRaw, 80);
        const outSolLamports = parseInt(q.outAmount ?? "0", 10);
        const pnl =
          Number.isFinite(costBasisLamports) && costBasisLamports > 0
            ? outSolLamports - costBasisLamports
            : null;
        quote = {
          outSolLamports,
          unrealizedPnlLamports: pnl,
          unrealizedPnlPct:
            pnl != null && costBasisLamports > 0 ? (pnl / costBasisLamports) * 100 : null,
          priceImpactPct: q.priceImpactPct ? parseFloat(q.priceImpactPct) * 100 : null,
        };
      } catch (err) {
        console.warn(
          `Live quote unavailable for ${mint.slice(0, 8)}: ${
            err instanceof Error ? err.message : String(err)
          }`
        );
      }
    }
    const body: TokenLiveBody = { pair, quote, checkedAt: Date.now() };
    tokenLiveCache.set(cacheKey, body);
    if (tokenLiveCache.size > 250) {
      const oldest = tokenLiveCache.keys().next().value;
      if (oldest) tokenLiveCache.delete(oldest);
    }
    return sendJson(res, 200, body);
  }

  if (req.method === "POST" && url === "/api/settings") {
    try {
      const body = await readJsonBody(req);
      const settings = normalizeFriendSettings(body);
      return sendJson(res, 200, { ok: true, settings: persistFriendSettings(settings) });
    } catch (err) {
      return sendError(res, 400, err instanceof Error ? err.message : String(err));
    }
  }

  if (req.method === "GET" && url.startsWith("/api/logs")) {
    const u = new URL(url, "http://localhost");
    const since = parseInt(u.searchParams.get("since") ?? "0", 10);
    return sendJson(res, 200, {
      logs: getLogs(Number.isFinite(since) ? since : 0),
    });
  }

  if (req.method === "POST" && url === "/api/start") {
    try {
      const body = await readJsonBody(req);
      let settings = getFriendBotSettings();
      if (Object.keys(body).length > 0) {
        settings = persistFriendSettings(normalizeFriendSettings(body));
      }
      await startBot();
      return sendJson(res, 200, { ok: true, settings });
    } catch (err) {
      return sendError(
        res,
        400,
        err instanceof Error ? err.message : String(err)
      );
    }
  }

  if (req.method === "POST" && url === "/api/stop") {
    stopBot();
    return sendJson(res, 200, { ok: true });
  }

  if (req.method === "POST" && url === "/api/actions/sell-all") {
    try {
      await sellAllPositions();
      return sendJson(res, 200, { ok: true, state: getBotState() });
    } catch (err) {
      return sendError(res, 400, err instanceof Error ? err.message : String(err));
    }
  }

  if (req.method === "POST" && url === "/api/actions/panic") {
    try {
      stopBot();
      await sellAllPositions();
      return sendJson(res, 200, { ok: true, state: getBotState() });
    } catch (err) {
      return sendError(res, 400, err instanceof Error ? err.message : String(err));
    }
  }

  if (req.method === "POST" && url === "/api/actions/close-all-sim") {
    try {
      await closeAllOpenPositionsSimulated();
      return sendJson(res, 200, { ok: true, state: getBotState() });
    } catch (err) {
      return sendError(res, 400, err instanceof Error ? err.message : String(err));
    }
  }

  if (req.method === "POST" && url === "/api/actions/reset-session") {
    resetTradingState();
    return sendJson(res, 200, { ok: true });
  }

  if (req.method === "POST" && url.startsWith("/api/actions/sell-position/")) {
    const index = parseInt(url.slice("/api/actions/sell-position/".length), 10);
    if (!Number.isInteger(index) || index <= 0) return sendError(res, 400, "Invalid position index");
    try {
      await sellPositionByIndex(index);
      return sendJson(res, 200, { ok: true, state: getBotState() });
    } catch (err) {
      return sendError(res, 400, err instanceof Error ? err.message : String(err));
    }
  }

  if (req.method === "POST" && url.startsWith("/api/actions/sell-position-half/")) {
    const index = parseInt(url.slice("/api/actions/sell-position-half/".length), 10);
    if (!Number.isInteger(index) || index <= 0) return sendError(res, 400, "Invalid position index");
    try {
      await sellPositionPercentByIndex(index, 0.5);
      return sendJson(res, 200, { ok: true, state: getBotState() });
    } catch (err) {
      return sendError(res, 400, err instanceof Error ? err.message : String(err));
    }
  }

  if (req.method === "POST" && url.startsWith("/api/actions/sell-position-percent/")) {
    const u = new URL(url, "http://localhost");
    const index = parseInt(u.pathname.slice("/api/actions/sell-position-percent/".length), 10);
    const fraction = parseFloat(u.searchParams.get("fraction") ?? "0");
    if (!Number.isInteger(index) || index <= 0) return sendError(res, 400, "Invalid position index");
    if (!Number.isFinite(fraction) || fraction <= 0 || fraction > 1) {
      return sendError(res, 400, "Invalid sell fraction");
    }
    try {
      await sellPositionPercentByIndex(index, fraction);
      return sendJson(res, 200, { ok: true, state: getBotState() });
    } catch (err) {
      return sendError(res, 400, err instanceof Error ? err.message : String(err));
    }
  }

  res.writeHead(404);
  res.end("Not found");
}

export function startServer(options: StartServerOptions = {}): void {
  const server = createServer((req, res) => {
    handle(req, res).catch((err) => {
      console.error("Server error:", err);
      try {
        sendError(res, 500, err instanceof Error ? err.message : String(err));
      } catch {
        // ignore
      }
    });
  });

  server.on("error", (err: NodeJS.ErrnoException) => {
    if (
      options.optional &&
      (err.code === "EADDRINUSE" || err.code === "EACCES" || err.code === "EADDRNOTAVAIL")
    ) {
      console.warn(
        `Web UI disabled: cannot listen on ${HOST}:${PORT} (${err.code}). ` +
          (err.code === "EADDRNOTAVAIL"
            ? "WEB_HOST is not an address on this machine (is Tailscale up? check `tailscale ip -4`)."
            : "Set WEB_PORT to a free port, or set BOT_CONTROL_API_ENABLED=false.")
      );
      server.close();
      return;
    }
    throw err;
  });

  server.listen(PORT, HOST, () => {
    console.log(`Web UI: http://${HOST}:${PORT}  ·  DEX UI: http://${HOST}:${PORT}/dex`);
  });

  startPriceSampler();
}
