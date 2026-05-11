import Client, {
  CommitmentLevel,
  type SubscribeRequest,
  type SubscribeUpdate,
  type SubscribeUpdateTransactionInfo,
} from "@triton-one/yellowstone-grpc";
import { PublicKey } from "@solana/web3.js";
import bs58 from "bs58";
import {
  shyftGrpcCommitment,
  shyftGrpcReconnectMs,
  shyftGrpcUrl,
  shyftGrpcXToken,
} from "./config.js";
import type { DetectedSwap } from "./types.js";
import type { SignatureDedupe } from "./watcher.js";

const WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112";

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function commitmentFromConfig(): CommitmentLevel {
  if (shyftGrpcCommitment === "confirmed") return CommitmentLevel.CONFIRMED;
  if (shyftGrpcCommitment === "finalized") return CommitmentLevel.FINALIZED;
  return CommitmentLevel.PROCESSED;
}

type YellowstoneClientConstructor = new (
  endpoint: string,
  xToken: string | undefined,
  channelOptions: undefined
) => {
  subscribe(): Promise<YellowstoneStream>;
};

type YellowstoneStream = {
  on(event: "data", listener: (update: SubscribeUpdate) => void): YellowstoneStream;
  on(event: "error", listener: (err: Error) => void): YellowstoneStream;
  on(event: "end" | "close", listener: () => void): YellowstoneStream;
  write(chunk: SubscribeRequest, cb: (err?: Error | null) => void): void;
  destroy(): void;
};

function pubkeyToBase58(bytes: Uint8Array): string {
  return new PublicKey(bytes).toBase58();
}

function signatureToBase58(bytes: Uint8Array): string {
  return bs58.encode(bytes);
}

function numberFromBigint(value: bigint): number | null {
  const n = Number(value);
  return Number.isSafeInteger(n) ? n : null;
}

type TokenSnap = { mint: string; amount: bigint; decimals: number };
type TokenNet = { net: bigint; decimals: number };

function addTokenNet(
  tokenNet: Map<string, TokenNet>,
  mint: string | undefined,
  amountRaw: bigint,
  decimals: number | undefined
): void {
  if (!mint || mint === WRAPPED_SOL_MINT || amountRaw === 0n) return;
  const entry = tokenNet.get(mint) ?? { net: 0n, decimals: decimals ?? 0 };
  entry.net += amountRaw;
  entry.decimals = decimals ?? entry.decimals;
  tokenNet.set(mint, entry);
}

function getBestTokenNet(tokenNet: Map<string, TokenNet>): {
  mint: string | null;
  net: bigint;
  abs: bigint;
  decimals: number;
} {
  let mint: string | null = null;
  let net = 0n;
  let abs = 0n;
  let decimals = 0;
  for (const [candidateMint, candidate] of tokenNet) {
    const candidateAbs = candidate.net < 0n ? -candidate.net : candidate.net;
    if (candidateAbs > abs) {
      mint = candidateMint;
      net = candidate.net;
      abs = candidateAbs;
      decimals = candidate.decimals;
    }
  }
  return { mint, net, abs, decimals };
}

export function extractSwapFromGrpcTransaction(
  info: SubscribeUpdateTransactionInfo,
  wallet: string
): DetectedSwap | null {
  if (!info.transaction?.message || !info.meta || info.meta.err) return null;

  const message = info.transaction.message;
  const meta = info.meta;
  const accountKeys = [
    ...message.accountKeys,
    ...meta.loadedWritableAddresses,
    ...meta.loadedReadonlyAddresses,
  ].map(pubkeyToBase58);
  const walletIdx = accountKeys.indexOf(wallet);
  if (walletIdx < 0) return null;

  const preLamports = BigInt(meta.preBalances[walletIdx] ?? "0");
  const postLamports = BigInt(meta.postBalances[walletIdx] ?? "0");
  const fee = BigInt(meta.fee ?? "0");
  const signerCount = message.header?.numRequiredSignatures ?? 0;
  const isSigner = walletIdx < signerCount;
  const nativeNet = postLamports - preLamports + (isSigner ? fee : 0n);

  const preMap = new Map<number, TokenSnap>();
  for (const balance of meta.preTokenBalances) {
    if (balance.owner !== wallet || balance.mint === WRAPPED_SOL_MINT) continue;
    preMap.set(balance.accountIndex, {
      mint: balance.mint,
      amount: BigInt(balance.uiTokenAmount?.amount ?? "0"),
      decimals: balance.uiTokenAmount?.decimals ?? 0,
    });
  }

  const tokenNet = new Map<string, TokenNet>();
  const handledIndices = new Set<number>();
  for (const balance of meta.postTokenBalances) {
    if (balance.owner !== wallet || balance.mint === WRAPPED_SOL_MINT) continue;
    handledIndices.add(balance.accountIndex);
    const pre = preMap.get(balance.accountIndex);
    const preAmount = pre?.amount ?? 0n;
    const postAmount = BigInt(balance.uiTokenAmount?.amount ?? "0");
    addTokenNet(
      tokenNet,
      balance.mint,
      postAmount - preAmount,
      balance.uiTokenAmount?.decimals ?? pre?.decimals
    );
  }
  for (const [accountIndex, pre] of preMap) {
    if (!handledIndices.has(accountIndex)) {
      addTokenNet(tokenNet, pre.mint, -pre.amount, pre.decimals);
    }
  }

  const best = getBestTokenNet(tokenNet);
  if (!best.mint || best.abs === 0n) return null;
  const signature = signatureToBase58(info.signature);

  if (nativeNet < 0n && best.net > 0n) {
    const amountRaw = numberFromBigint(-nativeNet);
    const counterpartyAmountRaw = numberFromBigint(best.net);
    if (amountRaw == null || counterpartyAmountRaw == null) return null;
    return {
      signature,
      wallet,
      inputMint: WRAPPED_SOL_MINT,
      outputMint: best.mint,
      amountRaw,
      isNativeInput: true,
      counterpartyAmountRaw,
      counterpartyDecimals: best.decimals,
    };
  }

  if (nativeNet > 0n && best.net < 0n) {
    const amountRaw = numberFromBigint(-best.net);
    const counterpartyAmountRaw = numberFromBigint(nativeNet);
    if (amountRaw == null || counterpartyAmountRaw == null) return null;
    return {
      signature,
      wallet,
      inputMint: best.mint,
      outputMint: WRAPPED_SOL_MINT,
      amountRaw,
      isNativeInput: false,
      counterpartyAmountRaw,
      counterpartyDecimals: 9,
    };
  }

  return null;
}

function buildRequest(trackedWallets: string[]): SubscribeRequest {
  return {
    accounts: {},
    slots: {},
    transactions: {
      trackedWallets: {
        vote: false,
        failed: false,
        signature: undefined,
        accountInclude: trackedWallets,
        accountExclude: [],
        accountRequired: [],
      },
    },
    transactionsStatus: {},
    blocks: {},
    blocksMeta: {},
    entry: {},
    accountsDataSlice: [],
    ping: undefined,
    commitment: commitmentFromConfig(),
  };
}

async function writeRequest(stream: YellowstoneStream, request: SubscribeRequest): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    stream.write(request, (err?: Error | null) => {
      if (err) reject(err);
      else resolve();
    });
  });
}

export type GrpcUnsubscribe = () => Promise<void>;

export async function subscribeGrpcWalletSwaps(
  trackedWallets: string[],
  processedSignatures: SignatureDedupe,
  onSwap: (swap: DetectedSwap) => void
): Promise<GrpcUnsubscribe> {
  if (!shyftGrpcUrl || !shyftGrpcXToken) {
    console.warn("gRPC: SHYFT_GRPC_URL or SHYFT_GRPC_X_TOKEN missing; skipping gRPC watcher.");
    return async () => {};
  }

  let stopped = false;
  let activeStream: { destroy: () => void } | null = null;
  const ClientCtor = Client as unknown as YellowstoneClientConstructor;
  const client = new ClientCtor(shyftGrpcUrl, shyftGrpcXToken, undefined);
  const request = buildRequest(trackedWallets);

  const run = async (): Promise<void> => {
    while (!stopped) {
      try {
        const stream = await client.subscribe();
        activeStream = stream;
        const streamClosed = new Promise<void>((resolve, reject) => {
          stream.on("error", (err) => {
            if (!stopped) reject(err);
            else resolve();
          });
          stream.on("end", resolve);
          stream.on("close", resolve);
        });

        stream.on("data", (update: SubscribeUpdate) => {
          const info = update.transaction?.transaction;
          if (!info) return;
          const signature = signatureToBase58(info.signature);
          if (processedSignatures.has(signature)) return;

          for (const wallet of trackedWallets) {
            const swap = extractSwapFromGrpcTransaction(info, wallet);
            if (!swap) continue;
            processedSignatures.add(signature);
            const ageMs = update.createdAt ? Date.now() - update.createdAt.getTime() : null;
            console.log(
              `gRPC detected swap ${signature.slice(0, 8)}...` +
                (ageMs != null && Number.isFinite(ageMs) ? ` (age ${ageMs}ms)` : "")
            );
            onSwap(swap);
            return;
          }
        });

        await writeRequest(stream, request);
        console.log(
          `gRPC: subscribed to ${trackedWallets.length} wallet(s) via ${shyftGrpcUrl} (${shyftGrpcCommitment})`
        );
        await streamClosed;
      } catch (err) {
        if (stopped) break;
        const msg = err instanceof Error ? err.message : String(err);
        console.warn(`gRPC watcher error: ${msg}; reconnecting in ${shyftGrpcReconnectMs}ms`);
        await sleep(shyftGrpcReconnectMs);
      } finally {
        activeStream = null;
      }
    }
  };

  void run();

  return async () => {
    stopped = true;
    activeStream?.destroy();
  };
}
