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
} from "./botService.js";
import { getQuote } from "./jupiter.js";
import { getLogs } from "./logBuffer.js";
import { WRAPPED_SOL_MINT } from "./tokenRegistry.js";

const PORT = parseInt(process.env.WEB_PORT ?? "3000", 10);

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = resolve(here, "..", "public");
const chartBundlePath = resolve(
  here,
  "..",
  "node_modules",
  "lightweight-charts",
  "dist",
  "lightweight-charts.standalone.production.js"
);
const quoteCache = new Map<
  string,
  {
    ts: number;
    mint: string;
    amountRaw: number;
    outLamports: number;
    valueSol: number;
    priceSol: number;
    priceImpactPct: string | null;
  }
>();
const QUOTE_CACHE_TTL_MS = 15_000;

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

function serveChartBundle(res: ServerResponse): void {
  try {
    const js = readFileSync(chartBundlePath);
    res.writeHead(200, {
      "Content-Type": "application/javascript; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
      "Content-Length": js.length,
    });
    res.end(js);
  } catch {
    res.writeHead(500);
    res.end("lightweight-charts bundle not found");
  }
}

function parsePositiveAmount(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return Math.floor(parsed);
}

function quoteCacheKey(mint: string, amountRaw: number, decimals: number): string {
  return `${mint}:${amountRaw}:${decimals}`;
}

async function handle(
  req: IncomingMessage,
  res: ServerResponse
): Promise<void> {
  const url = req.url ?? "/";

  if (req.method === "GET" && (url === "/" || url === "/index.html")) {
    return serveIndex(res);
  }

  if (req.method === "GET" && url === "/vendor/lightweight-charts.js") {
    return serveChartBundle(res);
  }

  if (req.method === "GET" && url === "/api/state") {
    return sendJson(res, 200, getBotState());
  }

  if (req.method === "GET" && url.startsWith("/api/logs")) {
    const u = new URL(url, "http://localhost");
    const since = parseInt(u.searchParams.get("since") ?? "0", 10);
    return sendJson(res, 200, {
      logs: getLogs(Number.isFinite(since) ? since : 0),
    });
  }

  if (req.method === "GET" && url.startsWith("/api/token-quote")) {
    const u = new URL(url, "http://localhost");
    const mint = u.searchParams.get("mint")?.trim();
    const amountRaw = parsePositiveAmount(u.searchParams.get("amountRaw"));
    const decimals = parseInt(u.searchParams.get("decimals") ?? "0", 10);

    if (!mint) return sendError(res, 400, "mint is required.");
    if (mint === WRAPPED_SOL_MINT) {
      return sendError(res, 400, "SOL does not need a token chart.");
    }
    if (amountRaw == null) {
      return sendError(res, 400, "amountRaw must be a positive number.");
    }
    if (!Number.isInteger(decimals) || decimals < 0 || decimals > 18) {
      return sendError(res, 400, "decimals must be an integer between 0 and 18.");
    }

    const cacheKey = quoteCacheKey(mint, amountRaw, decimals);
    const cached = quoteCache.get(cacheKey);
    if (cached && Date.now() - cached.ts < QUOTE_CACHE_TTL_MS) {
      return sendJson(res, 200, { ...cached, cached: true });
    }

    try {
      const quote = await getQuote(mint, WRAPPED_SOL_MINT, amountRaw, 80);
      const outLamports = parseInt(quote.outAmount ?? "0", 10);
      const tokens = amountRaw / Math.pow(10, decimals);
      const valueSol = outLamports / 1_000_000_000;
      const priceSol = tokens > 0 ? valueSol / tokens : 0;

      const body = {
        ts: Date.now(),
        mint,
        amountRaw,
        outLamports,
        valueSol,
        priceSol,
        priceImpactPct: quote.priceImpactPct ?? null,
      };
      quoteCache.set(cacheKey, body);
      return sendJson(res, 200, body);
    } catch (err) {
      if (cached) {
        return sendJson(res, 200, {
          ...cached,
          cached: true,
          stale: true,
          error: err instanceof Error ? err.message : String(err),
        });
      }
      return sendError(
        res,
        502,
        err instanceof Error ? err.message : String(err)
      );
    }
  }

  if (req.method === "POST" && url === "/api/start") {
    try {
      await startBot();
      return sendJson(res, 200, { ok: true });
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

  if (req.method === "POST" && url === "/api/sell-all") {
    console.log("Manual sell-all requested from web API.");
    void sellAllPositions();
    return sendJson(res, 202, { ok: true, action: "sell-all" });
  }

  if (req.method === "POST" && url.startsWith("/api/sell/")) {
    const index = parseInt(url.slice("/api/sell/".length), 10);
    if (!Number.isInteger(index) || index < 1) {
      return sendError(res, 400, "Sell index must be a positive integer.");
    }
    console.log(`Manual sell #${index} requested from web API.`);
    void sellPositionByIndex(index);
    return sendJson(res, 202, { ok: true, action: "sell", index });
  }

  res.writeHead(404);
  res.end("Not found");
}

export function startServer(): void {
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
    if (err.code === "EADDRINUSE") {
      console.warn(`Web UI not started: 127.0.0.1:${PORT} is already in use.`);
      return;
    }
    console.error("Web UI server error:", err);
  });
  server.listen(PORT, "127.0.0.1", () => {
    console.log(`Web UI: http://localhost:${PORT}`);
  });
}
