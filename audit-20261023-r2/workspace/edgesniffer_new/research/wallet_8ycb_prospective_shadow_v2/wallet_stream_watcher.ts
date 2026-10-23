import fs from "node:fs";
import path from "node:path";
import Client, { CommitmentLevel, txEncode } from "@triton-one/yellowstone-grpc";

const TARGET = "8YcbyX92UHTU23HZv3ccP9o13qibErQkKaUjoxqxd7SJ";
const SOL_MINT = "So11111111111111111111111111111111111111112";
const PUMP_PROGRAMS = new Set([
  "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
  "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",
]);
const OUT = process.env.SHADOW_WALLET_STREAM_FILE;
const ENDPOINT = process.env.SHYFT_GRPC_ENDPOINT;
const TOKEN = process.env.SHYFT_GRPC_TOKEN;
const MAX_BYTES = Number(process.env.SHADOW_WALLET_STREAM_MAX_BYTES ?? "268435456");
const RECONNECT_MS = Math.max(250, Number(process.env.SHYFT_GRPC_RECONNECT_MS ?? "1000"));

if (!OUT || !ENDPOINT || !TOKEN) throw new Error("read-only Shyft stream configuration is incomplete");
if (process.env.NO_SUBMIT_HARD_GUARD !== "8YCB_OBSERVATION_ONLY_V2") {
  throw new Error("NO_SUBMIT_HARD_GUARD mismatch");
}

type TokenBalance = {
  accountIndex?: number;
  mint?: string;
  owner?: string;
  uiTokenAmount?: { amount?: string; decimals?: number };
};

type Encoded = {
  transaction?: {
    signatures?: string[];
    message?: {
      accountKeys?: Array<string | { pubkey?: string }>;
      instructions?: Array<{ programId?: string; programIdIndex?: number }>;
    };
  };
  meta?: {
    preBalances?: number[];
    postBalances?: number[];
    preTokenBalances?: TokenBalance[];
    postTokenBalances?: TokenBalance[];
    innerInstructions?: Array<{ instructions?: Array<{ programId?: string; programIdIndex?: number }> }>;
    loadedAddresses?: { writable?: string[]; readonly?: string[] };
  };
  blockTime?: number;
};

fs.mkdirSync(path.dirname(OUT), { recursive: true });
let rows = 0;
let errors = 0;
let capped = false;

function append(row: Record<string, unknown>): void {
  if (capped) return;
  try {
    if (fs.existsSync(OUT) && fs.statSync(OUT).size >= MAX_BYTES) {
      capped = true;
      console.error(`wallet stream provenance cap reached: ${MAX_BYTES}`);
      return;
    }
    fs.appendFileSync(OUT, `${JSON.stringify(row)}\n`, { encoding: "utf8", mode: 0o640 });
    rows += 1;
  } catch (error) {
    errors += 1;
    console.error(`wallet stream append failed: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function keys(encoded: Encoded): string[] {
  return (encoded.transaction?.message?.accountKeys ?? [])
    .map((key) => (typeof key === "string" ? key : key.pubkey))
    .filter((key): key is string => Boolean(key));
}

function pumpProgramPresent(encoded: Encoded, accountKeys: string[]): boolean {
  const loaded = [...(encoded.meta?.loadedAddresses?.writable ?? []), ...(encoded.meta?.loadedAddresses?.readonly ?? [])];
  const allKeys = [...accountKeys, ...loaded];
  const instructions = [
    ...(encoded.transaction?.message?.instructions ?? []),
    ...((encoded.meta?.innerInstructions ?? []).flatMap((group) => group.instructions ?? [])),
  ];
  return instructions.some((instruction) => {
    const program = instruction.programId ??
      (typeof instruction.programIdIndex === "number" ? allKeys[instruction.programIdIndex] : undefined);
    return program != null && PUMP_PROGRAMS.has(program);
  });
}

function tokenDeltas(encoded: Encoded): Array<{ mint: string; decimals: number; raw: bigint }> {
  const byAccount = new Map<number, { mint: string; decimals: number; pre: bigint; post: bigint }>();
  for (const balance of encoded.meta?.preTokenBalances ?? []) {
    if (balance.accountIndex == null || balance.owner !== TARGET || !balance.mint) continue;
    byAccount.set(balance.accountIndex, {
      mint: balance.mint,
      decimals: balance.uiTokenAmount?.decimals ?? 6,
      pre: BigInt(balance.uiTokenAmount?.amount ?? "0"),
      post: 0n,
    });
  }
  for (const balance of encoded.meta?.postTokenBalances ?? []) {
    if (balance.accountIndex == null || balance.owner !== TARGET || !balance.mint) continue;
    const current = byAccount.get(balance.accountIndex) ?? {
      mint: balance.mint,
      decimals: balance.uiTokenAmount?.decimals ?? 6,
      pre: 0n,
      post: 0n,
    };
    current.post = BigInt(balance.uiTokenAmount?.amount ?? "0");
    current.decimals = balance.uiTokenAmount?.decimals ?? current.decimals;
    byAccount.set(balance.accountIndex, current);
  }
  const byMint = new Map<string, { mint: string; decimals: number; raw: bigint }>();
  for (const balance of byAccount.values()) {
    const current = byMint.get(balance.mint) ?? { mint: balance.mint, decimals: balance.decimals, raw: 0n };
    current.raw += balance.post - balance.pre;
    byMint.set(balance.mint, current);
  }
  return [...byMint.values()].filter((row) => row.mint !== SOL_MINT && row.raw !== 0n);
}

function integerJsonValue(value: unknown): string | number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "bigint") return value.toString();
  if (typeof value === "string" && value.length > 0) return value;
  if (value != null && typeof value === "object" && "toString" in value) {
    const rendered = String(value);
    if (rendered && rendered !== "[object Object]") return rendered;
  }
  return null;
}

function targetSwap(encoded: Encoded): Record<string, unknown> | undefined {
  const accountKeys = keys(encoded);
  if (!pumpProgramPresent(encoded, accountKeys)) return undefined;
  const targetIndexes = accountKeys.map((key, index) => key === TARGET ? index : -1).filter((index) => index >= 0);
  const pre = encoded.meta?.preBalances ?? [];
  const post = encoded.meta?.postBalances ?? [];
  const solDelta = targetIndexes.reduce((sum, index) => sum + ((post[index] ?? 0) - (pre[index] ?? 0)), 0);
  const best = tokenDeltas(encoded).sort((a, b) => {
    const aa = a.raw < 0n ? -a.raw : a.raw;
    const bb = b.raw < 0n ? -b.raw : b.raw;
    return aa === bb ? 0 : aa > bb ? -1 : 1;
  })[0];
  if (!best) return undefined;
  if (solDelta < 0 && best.raw > 0n) {
    return {
      wallet: TARGET, mint: best.mint, side: "buy", sourceBuySol: Math.abs(solDelta) / 1e9,
      tokensOutRaw: best.raw.toString(), timestamp: integerJsonValue(encoded.blockTime),
      sourceKind: "pump_program_balance_delta",
    };
  }
  if (solDelta > 0 && best.raw < 0n) {
    return {
      wallet: TARGET, mint: best.mint, side: "sell", sourceBuySol: solDelta / 1e9,
      sourceSellTokensRaw: (-best.raw).toString(), timestamp: integerJsonValue(encoded.blockTime),
      sourceKind: "pump_program_balance_delta",
    };
  }
  return undefined;
}

function decode(data: any): Record<string, unknown> | undefined {
  const receivedAtMs = Date.now();
  const callbackUnixNs = (BigInt(receivedAtMs) * 1_000_000n).toString();
  const callbackMonotonicNs = process.hrtime.bigint().toString();
  const txInfo = data?.transaction?.transaction;
  if (!txInfo) return undefined;
  let encoded: Encoded;
  try {
    encoded = txEncode.encode(txInfo, txEncode.encoding.Json ?? txEncode.encoding.JsonParsed, 0, false) as unknown as Encoded;
  } catch {
    return undefined;
  }
  const signature = encoded.transaction?.signatures?.[0];
  if (!signature) return undefined;
  const swap = targetSwap(encoded);
  return {
    schema: "8ycb-wallet-observation-v2",
    kind: "wallet_transaction_observation",
    targetWallet: TARGET,
    provider: "shyft_grpc",
    signature,
    slot: integerJsonValue(data?.transaction?.slot),
    providerReceivedAtMs: receivedAtMs,
    callbackUnixNs,
    callbackMonotonicNs,
    rawKind: "transaction",
    accountCount: keys(encoded).length,
    targetAccountPresent: keys(encoded).includes(TARGET),
    localSwaps: swap ? [swap] : [],
  };
}

function commitment(): CommitmentLevel {
  switch ((process.env.SHYFT_GRPC_COMMITMENT ?? "processed").toLowerCase()) {
    case "finalized": return CommitmentLevel.FINALIZED;
    case "confirmed": return CommitmentLevel.CONFIRMED;
    default: return CommitmentLevel.PROCESSED;
  }
}

function request(): any {
  return {
    accounts: {}, slots: {},
    transactions: { edge: { vote: false, failed: false, signature: undefined, accountInclude: [TARGET], accountExclude: [], accountRequired: [] } },
    transactionsStatus: {}, blocks: {}, blocksMeta: {}, entry: {}, accountsDataSlice: [], ping: undefined,
    commitment: commitment(),
  };
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function observeForever(): Promise<void> {
  for (;;) {
    try {
      const client = new Client(ENDPOINT, TOKEN, undefined);
      const stream = await client.subscribe();
      await new Promise<void>((resolve, reject) => {
        stream.write(request(), (error?: Error | null) => error ? reject(error) : resolve());
      });
      await new Promise<void>((resolve, reject) => {
        stream.on("data", (data: unknown) => {
          const row = decode(data);
          if (row) append(row);
        });
        stream.on("error", reject);
        stream.on("end", resolve);
        stream.on("close", resolve);
      });
    } catch (error) {
      errors += 1;
      append({
        schema: "8ycb-wallet-observation-v2", kind: "watcher_error", at: new Date().toISOString(),
        error: error instanceof Error ? error.message.slice(0, 1000) : String(error).slice(0, 1000),
      });
      await wait(RECONNECT_MS);
    }
  }
}

append({
  schema: "8ycb-wallet-observation-v2", kind: "watcher_start", targetWallet: TARGET,
  startedAt: new Date().toISOString(), pid: process.pid,
  note: "Account-filtered observation only; no transaction construction, signer, or submission module is imported.",
});
setInterval(() => {
  console.log(`8YCB wallet watcher health rows=${rows} errors=${errors} capped=${capped}`);
}, 60_000).unref();
void observeForever().catch((error) => {
  console.error(`unrecoverable observation error: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 2;
});
