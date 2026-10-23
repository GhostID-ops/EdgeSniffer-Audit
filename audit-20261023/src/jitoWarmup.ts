import {
  jitoBundleUrls,
  jitoConnectionWarmupEnabled,
  jitoConnectionWarmupMs,
  jitoTransactionUrls,
  jitoTipFloorUrl,
} from "./config.js";

let pollerStarted = false;
let pollerHandle: NodeJS.Timeout | null = null;
let lastWarningAt = 0;
let suppressedWarnings = 0;

const WARNING_GAP_MS = 60_000;

async function warmJsonRpc(url: string): Promise<void> {
  const body = JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "getTipAccounts",
    params: [],
  });
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
    signal: AbortSignal.timeout(1200),
  });
}

async function warmTipFloor(): Promise<void> {
  await fetch(jitoTipFloorUrl, {
    signal: AbortSignal.timeout(1200),
  });
}

function warmupUrls(): string[] {
  return Array.from(new Set([...jitoTransactionUrls, ...jitoBundleUrls])).filter(Boolean);
}

async function warmOnce(): Promise<void> {
  try {
    const urls = warmupUrls();
    const results = await Promise.allSettled([...urls.map((url) => warmJsonRpc(url)), warmTipFloor()]);
    const failed = results.filter((result) => result.status === "rejected");
    if (failed.length === 0) return;
    throw failed[0]!.reason;
  } catch (err) {
    const now = Date.now();
    suppressedWarnings += 1;
    if (now - lastWarningAt >= WARNING_GAP_MS) {
      const msg = err instanceof Error ? err.message : String(err);
      const suffix =
        suppressedWarnings > 1 ? ` (${suppressedWarnings - 1} similar warning(s) suppressed)` : "";
      console.warn(`Jito connection warmup failed: ${msg}${suffix}`);
      lastWarningAt = now;
      suppressedWarnings = 0;
    }
  }
}

export function startJitoConnectionWarmup(): void {
  if (pollerStarted) return;
  if (!jitoConnectionWarmupEnabled) {
    console.log("Jito connection warmup: OFF (set JITO_CONNECTION_WARMUP_ENABLED=true to enable).");
    return;
  }
  pollerStarted = true;
  const urls = warmupUrls();
  console.log(
    `Jito connection warmup: ON (${urls.length} Jito endpoint(s) + tip floor, every ${jitoConnectionWarmupMs}ms).`
  );
  void warmOnce();
  pollerHandle = setInterval(() => {
    void warmOnce();
  }, jitoConnectionWarmupMs);
}

export function stopJitoConnectionWarmup(): void {
  if (pollerHandle) clearInterval(pollerHandle);
  pollerHandle = null;
  pollerStarted = false;
}
