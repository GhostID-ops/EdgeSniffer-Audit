import {
  heliusTxSubscribeCommitment,
  heliusTxSubscribeDetails,
  heliusTxSubscribeUrl,
} from "./config.js";
import type { DetectedSwap } from "./types.js";
import { getRawTxSwap, type SignatureDedupe } from "./watcher.js";

export type HeliusTxUnsubscribe = () => Promise<void>;

type WsLike = {
  readyState: number;
  send(data: string): void;
  close(): void;
  addEventListener(event: "open", cb: () => void): void;
  addEventListener(event: "message", cb: (event: { data: unknown }) => void): void;
  addEventListener(event: "error", cb: (event: unknown) => void): void;
  addEventListener(event: "close", cb: () => void): void;
  ping?: () => void;
};

const WS_OPEN = 1;
const RECONNECT_MS = 1_000;
const RAW_PARSE_IN_FLIGHT = new Set<string>();
const LOCAL_SEEN = new Map<string, number>();
const LOCAL_SEEN_TTL_MS = 30_000;

function makeWebSocket(url: string): WsLike {
  const ctor = (globalThis as unknown as { WebSocket?: new (url: string) => WsLike }).WebSocket;
  if (!ctor) {
    throw new Error("Global WebSocket is unavailable. Run on Node 22+ or add a WebSocket dependency.");
  }
  return new ctor(url);
}

function short(sig: string): string {
  return `${sig.slice(0, 8)}…`;
}

function extractSignature(message: unknown): string | null {
  const obj = message as {
    params?: { result?: { signature?: unknown } };
    result?: unknown;
  };
  const sig = obj.params?.result?.signature;
  return typeof sig === "string" && sig.length > 0 ? sig : null;
}

function rpcErrorMessage(message: unknown): string | null {
  const error = (message as { error?: { code?: unknown; message?: unknown } }).error;
  if (!error) return null;
  const code = typeof error.code === "number" || typeof error.code === "string" ? ` ${error.code}` : "";
  const msg = typeof error.message === "string" ? error.message : JSON.stringify(error);
  return `RPC error${code}: ${msg}`;
}

async function eventDataToString(data: unknown): Promise<string> {
  if (typeof data === "string") return data;
  if (data instanceof ArrayBuffer) return Buffer.from(data).toString("utf8");
  if (ArrayBuffer.isView(data)) {
    return Buffer.from(data.buffer, data.byteOffset, data.byteLength).toString("utf8");
  }
  if (typeof Blob !== "undefined" && data instanceof Blob) {
    return await data.text();
  }
  return String(data);
}

function sweepLocalSeen(now: number): void {
  for (const [sig, ts] of LOCAL_SEEN) {
    if (now - ts > LOCAL_SEEN_TTL_MS) LOCAL_SEEN.delete(sig);
  }
}

/**
 * Helius Enhanced WebSocket transactionSubscribe watcher. It is intentionally race-only: it does
 * not mark a signature globally processed unless it successfully extracts a swap, so Shyft gRPC /
 * RabbitStream can still win with richer Pump.fun account data.
 */
export async function subscribeHeliusTransactionSwaps(
  trackedWallets: string[],
  processedSignatures: SignatureDedupe,
  onSwap: (swap: DetectedSwap) => void
): Promise<HeliusTxUnsubscribe> {
  if (!heliusTxSubscribeUrl) {
    console.warn("Helius transactionSubscribe: HELIUS_TX_SUBSCRIBE_URL/API key missing; skipping.");
    return async () => {};
  }
  if (trackedWallets.length === 0) {
    console.warn("Helius transactionSubscribe: no tracked wallets; skipping.");
    return async () => {};
  }

  let stopped = false;
  let ws: WsLike | null = null;
  let pingTimer: NodeJS.Timeout | null = null;
  let reconnectTimer: NodeJS.Timeout | null = null;
  let requestId = 9000;

  const connect = () => {
    if (stopped) return;
    ws = makeWebSocket(heliusTxSubscribeUrl);

    ws.addEventListener("open", () => {
      const req = {
        jsonrpc: "2.0",
        id: requestId++,
        method: "transactionSubscribe",
        params: [
          {
            vote: false,
            failed: false,
            accountInclude: trackedWallets,
          },
          {
            commitment: heliusTxSubscribeCommitment,
            encoding: "jsonParsed",
            transactionDetails: heliusTxSubscribeDetails,
            showRewards: false,
            maxSupportedTransactionVersion: 0,
          },
        ],
      };
      ws?.send(JSON.stringify(req));
      pingTimer = setInterval(() => {
        if (!ws || ws.readyState !== WS_OPEN) return;
        if (typeof ws.ping === "function") {
          ws.ping();
          return;
        }
        ws.send(JSON.stringify({ jsonrpc: "2.0", id: requestId++, method: "getHealth" }));
      }, 30_000);
      console.log(
        `Helius transactionSubscribe: subscribed to ${trackedWallets.length} wallet(s) ` +
          `(${heliusTxSubscribeCommitment}, details=${heliusTxSubscribeDetails})`
      );
    });

    ws.addEventListener("message", (event) => {
      void (async () => {
        const raw = await eventDataToString(event.data);
        let parsed: unknown;
        try {
          parsed = JSON.parse(raw);
        } catch {
          return;
        }
        const rpcError = rpcErrorMessage(parsed);
        if (rpcError) {
          console.warn(`Helius transactionSubscribe ${rpcError}`);
          return;
        }
        const sig = extractSignature(parsed);
        if (!sig) return;
        const now = Date.now();
        sweepLocalSeen(now);
        if (LOCAL_SEEN.has(sig) || RAW_PARSE_IN_FLIGHT.has(sig) || processedSignatures.has(sig)) return;
        LOCAL_SEEN.set(sig, now);
        RAW_PARSE_IN_FLIGHT.add(sig);
        try {
          for (const wallet of trackedWallets) {
            const swap = await getRawTxSwap(sig, wallet);
            if (!swap) continue;
            if (processedSignatures.has(sig)) return;
            processedSignatures.add(sig);
            console.log(`⚡ Helius txSubscribe detected swap ${short(sig)} (${Date.now() - now}ms)`);
            onSwap(swap);
            return;
          }
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          console.warn(`Helius txSubscribe parse skip ${short(sig)}: ${msg}`);
        } finally {
          RAW_PARSE_IN_FLIGHT.delete(sig);
        }
      })();
    });

    ws.addEventListener("error", (event) => {
      if (!stopped) console.warn(`Helius transactionSubscribe error: ${String(event)}`);
    });

    ws.addEventListener("close", () => {
      if (pingTimer) clearInterval(pingTimer);
      pingTimer = null;
      ws = null;
      if (!stopped) {
        reconnectTimer = setTimeout(connect, RECONNECT_MS);
      }
    });
  };

  connect();

  return async () => {
    stopped = true;
    if (pingTimer) clearInterval(pingTimer);
    if (reconnectTimer) clearTimeout(reconnectTimer);
    pingTimer = null;
    reconnectTimer = null;
    if (ws) {
      try {
        ws.close();
      } catch {
        // best effort
      }
    }
    ws = null;
  };
}
