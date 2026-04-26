import { createServer, IncomingMessage, ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { startBot, stopBot, getBotState } from "./botService.js";
import { getLogs } from "./logBuffer.js";

const PORT = parseInt(process.env.WEB_PORT ?? "3000", 10);

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = resolve(here, "..", "public");

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

async function handle(
  req: IncomingMessage,
  res: ServerResponse
): Promise<void> {
  const url = req.url ?? "/";

  if (req.method === "GET" && (url === "/" || url === "/index.html")) {
    return serveIndex(res);
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
  server.listen(PORT, "127.0.0.1", () => {
    console.log(`Web UI: http://localhost:${PORT}`);
  });
}
