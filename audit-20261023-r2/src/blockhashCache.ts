import { readConnection } from "./config.js";

/**
 * Background-refreshed blockhash cache.
 *
 * Solana blockhashes are valid for ~150 slots (~60s). Fetching one inline before every tx
 * build wastes 80-150ms per buy because the RPC round-trip blocks the hot path. We refresh
 * every 300ms in the background — well under the 60s expiry window — so the hot path can
 * read the cached value instantly.
 *
 * Failure modes:
 *   - First call before any refresh completes: fall back to inline fetch (slow but correct).
 *   - All refreshes fail for >8s (cache stale): inline-fetch fresh and reset cache.
 *   - Caller broadcasts and gets `BlockhashNotFound`: caller should invalidate the cache
 *     so the next refresh kicks immediately (see invalidate()).
 */
type BlockhashEntry = { blockhash: string; lastValidBlockHeight: number; fetchedAt: number };

const REFRESH_INTERVAL_MS = 300;
const STALE_THRESHOLD_MS = 8000;

let cached: BlockhashEntry | null = null;
let inflight: Promise<BlockhashEntry> | null = null;
let pollerStarted = false;
let pollerHandle: NodeJS.Timeout | null = null;

async function fetchFresh(): Promise<BlockhashEntry> {
  // Dedupe parallel callers: if a fetch is already in flight, wait on it instead of double-RPC.
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const result = await readConnection.getLatestBlockhash("confirmed");
      const entry: BlockhashEntry = {
        blockhash: result.blockhash,
        lastValidBlockHeight: result.lastValidBlockHeight,
        fetchedAt: Date.now(),
      };
      cached = entry;
      return entry;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/**
 * Synchronous-feeling accessor: returns the cached blockhash if fresh, otherwise awaits a
 * fetch. Hot path should always hit the cache when the background poller is running.
 */
export async function getCachedBlockhash(): Promise<{
  blockhash: string;
  lastValidBlockHeight: number;
}> {
  if (cached && Date.now() - cached.fetchedAt < STALE_THRESHOLD_MS) {
    return { blockhash: cached.blockhash, lastValidBlockHeight: cached.lastValidBlockHeight };
  }
  const fresh = await fetchFresh();
  return { blockhash: fresh.blockhash, lastValidBlockHeight: fresh.lastValidBlockHeight };
}

/**
 * Force the next read to skip the cache. Call this after a broadcast error like
 * `BlockhashNotFound` or `TransactionExpired` so the next tx uses a fresh blockhash.
 */
export function invalidateBlockhashCache(): void {
  cached = null;
}

export function startBlockhashCache(): void {
  if (pollerStarted) return;
  pollerStarted = true;
  // Kick once immediately so the first trade can hit cache.
  void fetchFresh().catch(() => {});
  pollerHandle = setInterval(() => {
    void fetchFresh().catch(() => {});
  }, REFRESH_INTERVAL_MS);
}

export function stopBlockhashCache(): void {
  if (pollerHandle) clearInterval(pollerHandle);
  pollerHandle = null;
  pollerStarted = false;
  cached = null;
  inflight = null;
}
