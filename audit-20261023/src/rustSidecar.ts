import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import {
  broadcastRaceEnabled,
  computeUnitLimit,
  executionMultiSendEnabled,
  jitoAuthUuid,
  jitoBundleUrls,
  jitoSendMode,
  jitoTransactionUrls,
  readRpcUrl,
  pumpSwapRustPreflight,
  rustExecutorEnabled,
  rustExecutorTimeoutMs,
  rustExitEarlySourceMaxPnlPct,
  rustExitEarlySourceWindowMs,
  rustExitRunnerDrawdownPct,
  rustExitMarkTelemetryMs,
  rustExitSourcePartialSellPct,
  rustExitStopLossGraceMs,
  rustExitStopLossPct,
  rustExitTakeProfitPct,
  rustExitTakeProfitSellPct,
  rustExitWatcherShadow,
  mirrorSlippageBps,
  rustSidecarBin,
  rustSidecarUseCargo,
  senderEndpointUrl,
  senderRpcFallbackEnabled,
  useJitoDirect,
  useJitoFallback,
} from "./config.js";
import { getCachedBlockhash } from "./blockhashCache.js";
import { getExecutionFeeParamsForRoute } from "./executionFeeCaps.js";
import type { DetectedSwap, RustHotBuy, SourcePumpSwapAccountMeta, SourceRaydiumCpmmAccountMeta } from "./types.js";
import type { SignatureDedupe } from "./watcher.js";

type RustSidecarEvent = {
  kind?: string;
  id?: string;
  source?: string;
  providerCreatedAtMs?: number;
  receivedAtMs?: number;
  detectedAtMs?: number;
  sourceSlot?: number;
  sourceTransactionIndex?: number;
  signature?: string;
  wallet?: string;
  inputMint?: string;
  outputMint?: string;
  amountRaw?: number;
  isNativeInput?: boolean;
  counterpartyAmountRaw?: number;
  sourceIxAccounts?: string[];
  sourceIxDataBase64?: string;
  sourcePumpSwapInstruction?: "buy" | "buy_exact_quote_in";
  sourcePumpSwapAccounts?: SourcePumpSwapAccountMeta[] | string[];
  sourceQuoteMint?: string;
  sourceQuoteAmountRaw?: number;
  sourceSignature?: string;
  mint?: string;
  solInLamports?: number;
  tokensOutRaw?: number;
  sourceAmountRaw?: number;
  route?: string;
  senderPath?: string;
  elapsedMs?: number;
  buildMs?: number;
  broadcastMs?: number;
  txBytes?: number;
  reason?: string;
  ok?: boolean;
  error?: string;
  actionPending?: boolean;
  bondingCurve?: string;
  sequence?: number;
  phase?: string;
  phaseBefore?: string;
  phaseAfterSuccess?: string;
  tokensHeldRaw?: number;
  tokensInRaw?: number;
  expectedSolOutLamports?: number;
  minSolOutLamports?: number;
  executableSolOutLamports?: number;
  netSolOutLamports?: number;
  remainingCostLamports?: number;
  pnlPct?: number;
  peakNetSolOutLamports?: number;
  slot?: number;
  observedAtMs?: number;
  shadow?: boolean;
};

type RustExecutorResponse = {
  kind?: string;
  id?: string;
  ok?: boolean;
  signature?: string;
  route?: string;
  senderPath?: string;
  solInLamports?: number;
  tokensOutRaw?: number;
  elapsedMs?: number;
  buildMs?: number;
  broadcastMs?: number;
  txBytes?: number;
  error?: string;
  actionPending?: boolean;
  phase?: string;
};

export type RustPumpFunBuyMode = "source-replay" | "direct";

export type RustPumpFunBuyArgs = {
  mode: RustPumpFunBuyMode;
  route: "pumpfun-source-replay-buy" | "pumpfun-direct-buy";
  mint: string;
  /** Source (tracked-wallet) tx being mirrored; feeds the executor's per-source-signature buy-once gate. */
  sourceSignature?: string;
  quoteMint?: string;
  solInLamports: number;
  tokensOutRaw?: number;
  minTokensOutRaw?: number;
  slippageBps: number;
  exactSolIn: boolean;
  sourceIxAccounts?: string[];
  sourceIxDataBase64?: string;
  bondingCurve?: string;
  creator?: string;
  tokenProgramId?: string;
  gateVirtualSolReserves?: number;
  gateVirtualTokenReserves?: number;
  gateReserveSlot?: number;
  gateReserveObservedAtMs?: number;
  nativeEntryDriftGate?: boolean;
  shadowEntryMarketCapUsd?: number;
  shadowTokensOutRaw?: number;
  shadowSolInLamports?: number;
  maxEntryDriftBps?: number;
  maxEntryMarketCapUsd?: number;
};

export type RustPumpSwapBuyArgs = {
  route: "pumpswap-direct-buy";
  mint: string;
  sourceSignature: string;
  buyLockOwner?: string;
  quoteMint?: string;
  /** Backward-compatible alias used by older PumpSwap callers. */
  solInLamports?: number;
  quoteAmountInRaw?: number;
  expectedBaseOutRaw: number;
  minBaseOutRaw: number;
  slippageBps: number;
  buyInstruction: "buy" | "buy_exact_quote_in";
  sourcePumpSwapAccounts: SourcePumpSwapAccountMeta[] | string[];
  baseTokenProgramId: string;
};

export type RustRaydiumCpmmSwapArgs = {
  route: "raydium-cpmm-swap-base-input";
  side: "buy" | "sell";
  mint: string;
  sourceSignature: string;
  inputMint: string;
  outputMint: string;
  amountInRaw: number;
  expectedOutRaw: number;
  minOutRaw: number;
  sourceRaydiumCpmmAccounts: SourceRaydiumCpmmAccountMeta[] | string[];
};
export type RustPumpSwapSellArgs = {
  route: "pumpswap-direct-sell";
  mint: string;
  tokensInRaw: number;
  expectedQuoteOutRaw: number;
  minQuoteOutRaw: number;
  sourcePumpSwapAccounts: SourcePumpSwapAccountMeta[] | string[];
  baseTokenProgramId: string;
};

export type RustPumpFunSellArgs = {
  route: "pumpfun-direct-sell" | "pumpfun-direct-sell-v2";
  mint: string;
  tokensInRaw: number;
  expectedSolOutLamports: number;
  minSolOutLamports: number;
  sellV2: boolean;
  accounts: SourcePumpSwapAccountMeta[];
};

export type RustPumpFunBuyResult =
  | {
      ok: true;
      signature: string;
      route: string;
      senderPath?: string;
      solInLamports: number;
      tokensOutRaw: number;
      elapsedMs?: number;
      buildMs?: number;
      broadcastMs?: number;
      txBytes?: number;
    }
  | { ok: false; error: string };

export type RustPumpSwapBuyResult = RustPumpFunBuyResult;
export type RustPumpSwapSellResult = RustPumpFunBuyResult;
export type RustRaydiumCpmmSwapResult = RustPumpFunBuyResult;
export type RustPumpFunSellResult = RustPumpFunBuyResult;
export type RustDryBuildResult = RustPumpFunBuyResult;

export type RustHotBuyFallback = {
  sourceSignature: string;
  wallet: string;
  mint: string;
  reason: string;
  sourceSlot?: number;
  sourceTransactionIndex?: number;
  detectedAtMs?: number;
  receivedAtMs: number;
};

export type RustExitEvent = {
  kind:
    | "rustExitMark"
    | "rustExitDecision"
    | "rustExitSellBroadcast"
    | "rustExitSellFailed"
    | "rustExitRouteFallback";
  mint: string;
  reason?: string;
  sequence?: number;
  signature?: string;
  tokensInRaw?: number;
  expectedSolOutLamports?: number;
  minSolOutLamports?: number;
  pnlPct?: number;
  phase?: string;
  shadow?: boolean;
  senderPath?: string;
  error?: string;
  slot?: number;
  observedAtMs?: number;
  executableSolOutLamports?: number;
  netSolOutLamports?: number;
  remainingCostLamports?: number;
  peakNetSolOutLamports?: number;
  phaseBefore?: string;
  phaseAfterSuccess?: string;
  elapsedMs?: number;
  buildMs?: number;
  broadcastMs?: number;
};

export type RustPumpFunExitWatchArgs = {
  mint: string;
  sourceWallet: string;
  bondingCurve: string;
  entryTsMs: number;
  tokensHeldRaw: number;
  costBasisLamports: number;
  buyConfirmed: boolean;
  sellV2: boolean;
  accounts: SourcePumpSwapAccountMeta[];
};

let executorStdin: NodeJS.WritableStream | null = null;
let executorSeq = 0;
const executorPending = new Map<
  string,
  {
    resolve: (value: any) => void;
    timer: NodeJS.Timeout;
  }
>();

function normalizePumpSwapAccountMeta(account: SourcePumpSwapAccountMeta | string, index: number): SourcePumpSwapAccountMeta {
  if (typeof account !== "string") {
    return {
      pubkey: account.pubkey,
      isSigner: account.isSigner,
      isWritable: account.isWritable,
    };
  }
  return {
    pubkey: account,
    isSigner: index === 1,
    isWritable: [0, 1, 5, 6, 7, 8, 10, 17, 20, 23].includes(index),
  };
}

function integerCommandField(value: number, field: string, min: number): number {
  if (!Number.isFinite(value)) {
    throw new Error(`${field} must be finite, got ${String(value)}`);
  }
  const integer = Math.floor(value);
  if (!Number.isSafeInteger(integer)) {
    throw new Error(`${field} exceeds JavaScript safe integer range, got ${String(value)}`);
  }
  if (integer < min) {
    throw new Error(`${field} must be >= ${min}, got ${String(value)}`);
  }
  return integer;
}

function optionalPositiveIntegerCommandField(value: number | undefined, field: string): number | undefined {
  if (value == null) return undefined;
  return integerCommandField(value, field, 1);
}

function parsePositiveEnvNumber(name: string): number | null {
  const raw = process.env[name];
  if (raw == null || raw.trim() === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function edgeLiveDustBuyCapLamports(): number | null {
  const edgeLiveDust =
    ["TWO_CQ_LIVE_DUST_ENABLED", "EDGE_LIVE_DUST_ENABLED", "CONFIRM_EDGE_LIVE_DUST"].some((name) => {
      const value = (process.env[name] ?? "").trim().toLowerCase();
      return value === "true" || value === "1" || value === "yes" || value === "on";
    });
  if (!edgeLiveDust) return null;

  const configuredCapSol =
    parsePositiveEnvNumber("EDGE_LIVE_MAX_RUST_BUY_SOL") ??
    parsePositiveEnvNumber("EDGE_LIVE_MAX_BUY_SOL") ??
    parsePositiveEnvNumber("EDGE_LIVE_BUY_SOL") ??
    0.01;
  return Math.floor(configuredCapSol * 1_000_000_000);
}

function commandValidationFailure(err: unknown): RustPumpFunBuyResult {
  const message = err instanceof Error ? err.message : String(err);
  return { ok: false, error: `Rust executor command validation failed: ${message}` };
}

export type RustSidecarUnsubscribe = () => Promise<void>;

function envFlag(name: string, defaultValue = false): boolean {
  const raw = process.env[name];
  if (raw == null || raw === "") return defaultValue;
  const normalized = raw.toLowerCase();
  return normalized === "true" || normalized === "1" || normalized === "yes" || normalized === "on";
}

function toSwap(event: RustSidecarEvent, nodeReceivedAtMs: number): DetectedSwap | null {
  if (event.kind !== "swap") return null;
  if (
    typeof event.signature !== "string" ||
    typeof event.wallet !== "string" ||
    typeof event.inputMint !== "string" ||
    typeof event.outputMint !== "string" ||
    typeof event.amountRaw !== "number" ||
    typeof event.isNativeInput !== "boolean"
  ) {
    return null;
  }
  return {
    signature: event.signature,
    wallet: event.wallet,
    detector: event.source === "jito-shredstream" ? "jito-shredstream" : "rust-sidecar",
    providerCreatedAtMs:
      typeof event.providerCreatedAtMs === "number" && Number.isFinite(event.providerCreatedAtMs)
        ? event.providerCreatedAtMs
        : undefined,
    receivedAtMs:
      typeof event.receivedAtMs === "number" && Number.isFinite(event.receivedAtMs)
        ? event.receivedAtMs
        : nodeReceivedAtMs,
    detectedAtMs:
      typeof event.detectedAtMs === "number" && Number.isFinite(event.detectedAtMs)
        ? event.detectedAtMs
        : nodeReceivedAtMs,
    sourceSlot:
      typeof event.sourceSlot === "number" && Number.isFinite(event.sourceSlot)
        ? event.sourceSlot
        : undefined,
    sourceTransactionIndex:
      typeof event.sourceTransactionIndex === "number" && Number.isFinite(event.sourceTransactionIndex)
        ? event.sourceTransactionIndex
        : undefined,
    inputMint: event.inputMint,
    outputMint: event.outputMint,
    amountRaw: event.amountRaw,
    isNativeInput: event.isNativeInput,
    counterpartyAmountRaw:
      typeof event.counterpartyAmountRaw === "number" ? event.counterpartyAmountRaw : undefined,
    sourceIxAccounts: Array.isArray(event.sourceIxAccounts) ? event.sourceIxAccounts : undefined,
    sourceIxDataBase64:
      typeof event.sourceIxDataBase64 === "string" ? event.sourceIxDataBase64 : undefined,
    sourcePumpSwapInstruction:
      event.sourcePumpSwapInstruction === "buy" || event.sourcePumpSwapInstruction === "buy_exact_quote_in"
        ? event.sourcePumpSwapInstruction
        : undefined,
    sourcePumpSwapAccounts: Array.isArray(event.sourcePumpSwapAccounts)
      ? event.sourcePumpSwapAccounts
      : undefined,
    sourceQuoteMint: typeof event.sourceQuoteMint === "string" ? event.sourceQuoteMint : undefined,
    sourceQuoteAmountRaw:
      typeof event.sourceQuoteAmountRaw === "number" ? event.sourceQuoteAmountRaw : undefined,
  };
}

function commandForSidecar(): { command: string; args: string[]; label: string } | null {
  if (rustSidecarUseCargo) {
    const args = ["run", "--manifest-path", "rust-sidecar/Cargo.toml", "--release"];
    if (envFlag("JITO_SHREDSTREAM_ENABLED")) args.push("--features", "jito-shredstream");
    return {
      command: "cargo",
      args,
      label: `cargo ${args.join(" ")}`,
    };
  }

  const bin = resolve(rustSidecarBin);
  if (!existsSync(bin)) {
    console.warn(
      `Rust sidecar binary not found at ${bin}. Run npm run rust-sidecar:build or set RUST_SIDECAR_USE_CARGO=true.`
    );
    return null;
  }
  return { command: bin, args: [], label: bin };
}

export async function startRustPumpFunSidecar(
  processedSignatures: SignatureDedupe,
  onSwap: (swap: DetectedSwap) => void,
  onHotBuy?: (hotBuy: RustHotBuy) => void,
  onHotBuyFallback?: (fallback: RustHotBuyFallback) => void,
  onRustExitEvent?: (event: RustExitEvent) => void,
  onUnavailable?: (reason: string) => void
): Promise<RustSidecarUnsubscribe> {
  const jitoShredstreamEnabled = envFlag("JITO_SHREDSTREAM_ENABLED");
  const jitoShredstreamGrpcUrl = process.env.JITO_SHREDSTREAM_GRPC_URL ?? "http://127.0.0.1:9999";
  const yellowstoneEnabled = envFlag("RUST_SIDECAR_YELLOWSTONE_ENABLED", true);
  const rustHotSniperEnabled =
    envFlag("RUST_HOT_SNIPER_ENABLED") && !envFlag("SIMULATION_MODE") && !envFlag("SHADOW_EXECUTION_MODE");
  console.log(
    `Rust sidecar config: Yellowstone=${yellowstoneEnabled ? "ON" : "OFF"}, ` +
      `Jito ShredStream=${jitoShredstreamEnabled ? "ON" : "OFF"}` +
      (jitoShredstreamEnabled ? ` (${jitoShredstreamGrpcUrl})` : "") +
      `, Rust executor=${rustExecutorEnabled ? "ON" : "OFF"}` +
      `, Rust hot sniper=${rustHotSniperEnabled ? "ON" : "OFF"}`
  );
  console.log(
    `Execution fee config: buy priority=${getExecutionFeeParamsForRoute("buy").priorityFeeMicroLamports} microLamports/CU, ` +
      `sell priority=${getExecutionFeeParamsForRoute("sell").priorityFeeMicroLamports} microLamports/CU, ` +
      `buy Jito tip=${getExecutionFeeParamsForRoute("buy").jitoTipLamports} lamports, ` +
      `sell Jito tip=${getExecutionFeeParamsForRoute("sell").jitoTipLamports} lamports, ` +
      `buy Helius tip=${getExecutionFeeParamsForRoute("buy").heliusTipLamports} lamports, ` +
      `sell Helius tip=${getExecutionFeeParamsForRoute("sell").heliusTipLamports} lamports, ` +
      `buy cap=${formatFeeCap(getExecutionFeeParamsForRoute("buy"))}, ` +
      `Jito direct=${useJitoDirect ? "ON" : "OFF"}, fallback=${useJitoFallback ? "ON" : "OFF"}, ` +
      `sendMode=${jitoSendMode}, race=${(executionMultiSendEnabled || broadcastRaceEnabled) ? "ON" : "OFF"}`
  );
  if (rustHotSniperEnabled) {
    console.log(
      `Rust hot sniper fee env: priority=${process.env.RUST_HOT_SNIPER_PRIORITY_FEE_MICRO_LAMPORTS ?? "auto"} microLamports/CU, ` +
        `Jito tip=${process.env.RUST_HOT_SNIPER_JITO_TIP_LAMPORTS ?? "auto"} lamports, ` +
        `Helius tip=${process.env.RUST_HOT_SNIPER_HELIUS_TIP_LAMPORTS ?? "auto"} lamports`
    );
  }

  const command = commandForSidecar();
  if (!command) {
    if (jitoShredstreamEnabled) {
      console.warn("Jito ShredStream is enabled, but the Rust sidecar could not be launched.");
    }
    return async () => {};
  }

  const child = spawn(command.command, command.args, {
    cwd: process.cwd(),
    env: process.env,
    stdio: [rustExecutorEnabled ? "pipe" : "ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let proc: ChildProcess | null = child;
  if (rustExecutorEnabled && child.stdin) {
    executorStdin = child.stdin;
    console.log(
      "Rust executor: command bridge ready; direct/source-replay buy broadcast logs appear after an eligible live buy."
    );
  }

  console.log(`Rust sidecar: started ${command.label}`);
  if (jitoShredstreamEnabled) {
    console.log("Jito ShredStream: waiting for Rust sidecar subscription confirmation...");
  }

  // A spawn failure (e.g. `cargo`/binary not on PATH) emits an async 'error' event. Without a
  // listener Node treats it as an unhandled error and kills the whole bot — which is exactly the
  // "works on my laptop, crashes on my desktop" case when Rust isn't installed. Degrade instead:
  // warn with a fix hint and let the WebSocket/gRPC watcher carry detection.
  child.on("error", (err) => {
    proc = null;
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ENOENT") {
      console.warn(
        `Rust sidecar could not start: '${command.command}' was not found on PATH. ` +
          (rustSidecarUseCargo
            ? "Install Rust via https://rustup.rs so 'cargo' is available, OR build the binary " +
              "(npm run rust-sidecar:build) and set RUST_SIDECAR_USE_CARGO=false, OR disable it with RUST_SIDECAR_ENABLED=false."
            : "Build it with 'npm run rust-sidecar:build', or disable it with RUST_SIDECAR_ENABLED=false.") +
          " Continuing without the sidecar; the WebSocket/gRPC watcher still runs."
      );
    } else {
      const msg = err instanceof Error ? err.message : String(err);
      console.warn(`Rust sidecar process error: ${msg}. Continuing without the sidecar.`);
    }
    rejectAllExecutorPending("Rust sidecar process error");
    executorStdin = null;
    onUnavailable?.("Rust sidecar process error");
  });

  if (!child.stdout || !child.stderr) {
    console.warn("Rust sidecar: stdout/stderr unavailable; sidecar disabled.");
    child.kill();
    return async () => {};
  }

  const stdout = createInterface({ input: child.stdout });
  const stderr = createInterface({ input: child.stderr });

  stdout.on("line", (line) => {
    const nodeReceivedAtMs = Date.now();
    if (!line.trim()) return;
    let event: RustSidecarEvent;
    try {
      event = JSON.parse(line) as RustSidecarEvent;
    } catch {
      console.log(`Rust sidecar: ${line}`);
      return;
    }

    const execResponse = toExecutorResponse(event);
    if (execResponse) {
      handleExecutorResponse(execResponse);
      return;
    }

    const rustExitEvent = toRustExitEvent(event);
    if (rustExitEvent) {
      onRustExitEvent?.(rustExitEvent);
      return;
    }

    const hotBuy = toHotBuy(event, nodeReceivedAtMs);
    if (hotBuy) {
      processedSignatures.add(hotBuy.sourceSignature);
      console.log(
        `Rust hot sniper buy ${hotBuy.mint.slice(0, 8)}... ` +
          `${hotBuy.signature.slice(0, 8)}...` +
          (hotBuy.elapsedMs != null ? ` (${hotBuy.elapsedMs}ms)` : "")
      );
      onHotBuy?.(hotBuy);
      return;
    }

    const hotBuyFallback = toHotBuyFallback(event, nodeReceivedAtMs);
    if (hotBuyFallback) {
      onHotBuyFallback?.(hotBuyFallback);
      return;
    }

    const swap = toSwap(event, nodeReceivedAtMs);
    if (!swap) return;
    if (processedSignatures.has(swap.signature)) return;
    processedSignatures.add(swap.signature);

    const ageMs =
      typeof event.detectedAtMs === "number" && Number.isFinite(event.detectedAtMs)
        ? nodeReceivedAtMs - event.detectedAtMs
        : null;
    console.log(
      `Rust sidecar detected ${swap.inputMint === "So11111111111111111111111111111111111111112" || swap.inputMint === "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v" ? "buy" : "sell"} ` +
        `${swap.signature.slice(0, 8)}...` +
        (ageMs != null ? ` (handoff ${ageMs}ms)` : "")
    );
    onSwap(swap);
  });

  stderr.on("line", (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    console.log(trimmed);
    if (trimmed.includes("subscribed to Jito ShredStream entries")) {
      console.log("Jito ShredStream: CONNECTED and streaming entries into the bot.");
    } else if (trimmed.includes("JITO_SHREDSTREAM_ENABLED=true but this binary was built without")) {
      console.warn("Jito ShredStream: NOT ACTIVE. Rebuild with npm run rust-sidecar:jito-build.");
    } else if (trimmed.includes("Jito ShredStream error")) {
      console.warn("Jito ShredStream: connection error reported by Rust sidecar.");
    }
  });

  child.on("exit", (code, signal) => {
    const reason = `Rust sidecar exited (${signal ?? code ?? "unknown"})`;
    if (proc) {
      console.warn(`${reason}.`);
      onUnavailable?.(reason);
    }
    rejectAllExecutorPending(reason);
    executorStdin = null;
  });

  return async () => {
    const running = proc;
    proc = null;
    stdout.close();
    stderr.close();
    if (running && !running.killed) {
      running.kill();
    }
    rejectAllExecutorPending("Rust sidecar stopped");
    executorStdin = null;
  };
}

function toHotBuyFallback(event: RustSidecarEvent, nodeReceivedAtMs: number): RustHotBuyFallback | null {
  if (event.kind !== "hotBuyFallback") return null;
  if (
    typeof event.sourceSignature !== "string" ||
    typeof event.wallet !== "string" ||
    typeof event.mint !== "string" ||
    typeof event.reason !== "string"
  ) {
    return null;
  }
  return {
    sourceSignature: event.sourceSignature,
    wallet: event.wallet,
    mint: event.mint,
    reason: event.reason,
    sourceSlot:
      typeof event.sourceSlot === "number" && Number.isFinite(event.sourceSlot)
        ? event.sourceSlot
        : undefined,
    sourceTransactionIndex:
      typeof event.sourceTransactionIndex === "number" && Number.isFinite(event.sourceTransactionIndex)
        ? event.sourceTransactionIndex
        : undefined,
    detectedAtMs:
      typeof event.detectedAtMs === "number" && Number.isFinite(event.detectedAtMs)
        ? event.detectedAtMs
        : undefined,
    receivedAtMs: nodeReceivedAtMs,
  };
}

function toHotBuy(event: RustSidecarEvent, nodeReceivedAtMs: number): RustHotBuy | null {
  if (event.kind !== "hotBuy") return null;
  if (
    typeof event.sourceSignature !== "string" ||
    typeof event.wallet !== "string" ||
    typeof event.mint !== "string" ||
    typeof event.signature !== "string" ||
    typeof event.sourceAmountRaw !== "number" ||
    typeof event.solInLamports !== "number" ||
    typeof event.tokensOutRaw !== "number"
  ) {
    return null;
  }
  return {
    sourceSignature: event.sourceSignature,
    wallet: event.wallet,
    mint: event.mint,
    signature: event.signature,
    sourceAmountRaw: event.sourceAmountRaw,
    solInLamports: event.solInLamports,
    tokensOutRaw: event.tokensOutRaw,
    route: typeof event.route === "string" ? event.route : undefined,
    senderPath: typeof event.senderPath === "string" ? event.senderPath : undefined,
    elapsedMs: typeof event.elapsedMs === "number" ? event.elapsedMs : undefined,
    buildMs: typeof event.buildMs === "number" ? event.buildMs : undefined,
    broadcastMs: typeof event.broadcastMs === "number" ? event.broadcastMs : undefined,
    txBytes: typeof event.txBytes === "number" ? event.txBytes : undefined,
    sourceSlot:
      typeof event.sourceSlot === "number" && Number.isFinite(event.sourceSlot)
        ? event.sourceSlot
        : undefined,
    sourceTransactionIndex:
      typeof event.sourceTransactionIndex === "number" && Number.isFinite(event.sourceTransactionIndex)
        ? event.sourceTransactionIndex
        : undefined,
    detectedAtMs:
      typeof event.detectedAtMs === "number" && Number.isFinite(event.detectedAtMs)
        ? event.detectedAtMs
        : undefined,
    receivedAtMs: nodeReceivedAtMs,
  };
}

function toExecutorResponse(event: RustSidecarEvent | RustExecutorResponse): RustExecutorResponse | null {
  if (event.kind !== "execResult" && event.kind !== "rustExitWatchResult") return null;
  if (typeof event.id !== "string") return null;
  return event as RustExecutorResponse;
}

function toRustExitEvent(event: RustSidecarEvent): RustExitEvent | null {
  if (
    event.kind !== "rustExitMark" &&
    event.kind !== "rustExitDecision" &&
    event.kind !== "rustExitSellBroadcast" &&
    event.kind !== "rustExitSellFailed" &&
    event.kind !== "rustExitRouteFallback"
  ) {
    return null;
  }
  if (typeof event.mint !== "string") return null;
  return {
    kind: event.kind,
    mint: event.mint,
    reason: typeof event.reason === "string" ? event.reason : undefined,
    sequence: typeof event.sequence === "number" ? event.sequence : undefined,
    signature: typeof event.signature === "string" ? event.signature : undefined,
    tokensInRaw: typeof event.tokensInRaw === "number" ? event.tokensInRaw : undefined,
    expectedSolOutLamports:
      typeof event.expectedSolOutLamports === "number" ? event.expectedSolOutLamports : undefined,
    minSolOutLamports:
      typeof event.minSolOutLamports === "number" ? event.minSolOutLamports : undefined,
    pnlPct: typeof event.pnlPct === "number" ? event.pnlPct : undefined,
    phase: typeof event.phase === "string" ? event.phase : undefined,
    shadow: typeof event.shadow === "boolean" ? event.shadow : undefined,
    senderPath: typeof event.senderPath === "string" ? event.senderPath : undefined,
    error: typeof event.error === "string" ? event.error : undefined,
    slot: typeof event.slot === "number" ? event.slot : undefined,
    observedAtMs: typeof event.observedAtMs === "number" ? event.observedAtMs : undefined,
    executableSolOutLamports:
      typeof event.executableSolOutLamports === "number" ? event.executableSolOutLamports : undefined,
    netSolOutLamports: typeof event.netSolOutLamports === "number" ? event.netSolOutLamports : undefined,
    remainingCostLamports:
      typeof event.remainingCostLamports === "number" ? event.remainingCostLamports : undefined,
    peakNetSolOutLamports:
      typeof event.peakNetSolOutLamports === "number" ? event.peakNetSolOutLamports : undefined,
    phaseBefore: typeof event.phaseBefore === "string" ? event.phaseBefore : undefined,
    phaseAfterSuccess: typeof event.phaseAfterSuccess === "string" ? event.phaseAfterSuccess : undefined,
    elapsedMs: typeof event.elapsedMs === "number" ? event.elapsedMs : undefined,
    buildMs: typeof event.buildMs === "number" ? event.buildMs : undefined,
    broadcastMs: typeof event.broadcastMs === "number" ? event.broadcastMs : undefined,
  };
}

function handleExecutorResponse(response: RustExecutorResponse): void {
  const id = response.id!;
  const pending = executorPending.get(id);
  if (!pending) return;
  executorPending.delete(id);
  clearTimeout(pending.timer);
  if (response.ok === true && typeof response.signature === "string") {
    pending.resolve({
      ok: true,
      signature: response.signature,
      route: response.route ?? "pumpfun-rust",
      senderPath: response.senderPath,
      solInLamports: response.solInLamports ?? 0,
      tokensOutRaw: response.tokensOutRaw ?? 1,
      elapsedMs: response.elapsedMs,
      buildMs: response.buildMs,
      broadcastMs: response.broadcastMs,
      txBytes: response.txBytes,
    });
    return;
  }
  if (response.ok === true) {
    pending.resolve({
      ok: true,
      actionPending: response.actionPending === true,
      phase: response.phase,
    });
    return;
  }
  pending.resolve({ ok: false, error: response.error ?? "Rust executor failed" });
}

function rejectAllExecutorPending(reason: string): void {
  for (const [id, pending] of executorPending) {
    executorPending.delete(id);
    clearTimeout(pending.timer);
    pending.resolve({ ok: false, error: reason });
  }
}

export async function executeRustPumpFunBuy(args: RustPumpFunBuyArgs): Promise<RustPumpFunBuyResult | null> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) return null;
  const id = `rust-exec-${Date.now()}-${++executorSeq}`;
  const { blockhash } = await getCachedBlockhash();
  let solInLamports: number;
  let tokensOutRaw: number | undefined;
  let minTokensOutRaw: number | undefined;
  let slippageBps: number;
  try {
    solInLamports = integerCommandField(args.solInLamports, "solInLamports", 1);
    tokensOutRaw = optionalPositiveIntegerCommandField(args.tokensOutRaw, "tokensOutRaw");
    minTokensOutRaw = optionalPositiveIntegerCommandField(args.minTokensOutRaw, "minTokensOutRaw");
    slippageBps = integerCommandField(args.slippageBps, "slippageBps", 0);
  } catch (err) {
    return commandValidationFailure(err);
  }
  const edgeCapLamports = edgeLiveDustBuyCapLamports();
  if (edgeCapLamports != null && solInLamports > edgeCapLamports) {
    return {
      ok: false,
      error:
        `Edge live dust buy cap refused ${solInLamports} lamports for ${args.mint}; ` +
        `cap=${edgeCapLamports} lamports`,
    };
  }
  const fees = getExecutionFeeParamsForRoute(args.route, solInLamports);
  const command = {
    kind: "executePumpfunBuy",
    id,
    mode: args.mode,
    route: args.route,
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    quoteMint: args.quoteMint ?? "So11111111111111111111111111111111111111112",
    solInLamports,
    tokensOutRaw,
    minTokensOutRaw,
    slippageBps,
    exactSolIn: args.exactSolIn,
    sourceIxAccounts: args.sourceIxAccounts,
    sourceIxDataBase64: args.sourceIxDataBase64,
    bondingCurve: args.bondingCurve,
    creator: args.creator,
    tokenProgramId: args.tokenProgramId,
    gateVirtualSolReserves: args.gateVirtualSolReserves,
    gateVirtualTokenReserves: args.gateVirtualTokenReserves,
    gateReserveSlot: args.gateReserveSlot,
    gateReserveObservedAtMs: args.gateReserveObservedAtMs,
    nativeEntryDriftGate: args.nativeEntryDriftGate,
    shadowEntryMarketCapUsd: args.shadowEntryMarketCapUsd,
    shadowTokensOutRaw: args.shadowTokensOutRaw,
    shadowSolInLamports: args.shadowSolInLamports,
    maxEntryDriftBps: args.maxEntryDriftBps,
    maxEntryMarketCapUsd: args.maxEntryMarketCapUsd,
    blockhash,
    computeUnitLimit,
    priorityFeeMicroLamports: fees.priorityFeeMicroLamports,
    jitoTipLamports: fees.jitoTipLamports,
    heliusTipLamports: fees.heliusTipLamports,
    useJitoDirect,
    useJitoFallback,
    senderRpcFallbackEnabled,
    executionMultiSendEnabled: executionMultiSendEnabled || broadcastRaceEnabled,
    jitoSendMode,
    jitoTransactionUrls,
    jitoBundleUrls,
    senderEndpointUrl,
    rpcFallbackUrl: readRpcUrl,
    jitoAuthUuid,
  };

  return await new Promise<RustPumpFunBuyResult>((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `Rust executor timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    const ok = executorStdin!.write(`${JSON.stringify(command)}\n`);
    if (!ok) {
      // Backpressure here is unusual; the timeout remains as a safety net.
    }
  });
}

export async function dryBuildRustPumpFunBuy(args: RustPumpFunBuyArgs): Promise<RustDryBuildResult | null> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) return null;
  const id = `rust-dry-pumpfun-buy-${Date.now()}-${++executorSeq}`;
  const { blockhash } = await getCachedBlockhash();
  let solInLamports: number;
  let tokensOutRaw: number | undefined;
  let slippageBps: number;
  try {
    solInLamports = integerCommandField(args.solInLamports, "solInLamports", 1);
    tokensOutRaw = optionalPositiveIntegerCommandField(args.tokensOutRaw, "tokensOutRaw");
    slippageBps = integerCommandField(args.slippageBps, "slippageBps", 0);
  } catch (err) {
    return commandValidationFailure(err);
  }
  const fees = getExecutionFeeParamsForRoute(args.route, solInLamports);
  const command = {
    kind: "dryBuildPumpfunBuy",
    id,
    mode: args.mode,
    route: args.route,
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    quoteMint: args.quoteMint ?? "So11111111111111111111111111111111111111112",
    solInLamports,
    tokensOutRaw,
    slippageBps,
    exactSolIn: args.exactSolIn,
    sourceIxAccounts: args.sourceIxAccounts,
    sourceIxDataBase64: args.sourceIxDataBase64,
    bondingCurve: args.bondingCurve,
    creator: args.creator,
    tokenProgramId: args.tokenProgramId,
    gateVirtualSolReserves: args.gateVirtualSolReserves,
    gateVirtualTokenReserves: args.gateVirtualTokenReserves,
    gateReserveSlot: args.gateReserveSlot,
    gateReserveObservedAtMs: args.gateReserveObservedAtMs,
    blockhash,
    computeUnitLimit,
    priorityFeeMicroLamports: fees.priorityFeeMicroLamports,
    jitoTipLamports: fees.jitoTipLamports,
    heliusTipLamports: fees.heliusTipLamports,
    useJitoDirect,
    useJitoFallback,
    senderRpcFallbackEnabled,
    executionMultiSendEnabled: executionMultiSendEnabled || broadcastRaceEnabled,
    jitoSendMode,
    jitoTransactionUrls,
    jitoBundleUrls,
    senderEndpointUrl,
    rpcFallbackUrl: readRpcUrl,
    jitoAuthUuid,
  };

  return await new Promise<RustDryBuildResult>((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `Rust Pump.fun dry-build timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    executorStdin!.write(`${JSON.stringify(command)}\n`);
  });
}

function formatFeeCap(fees: ReturnType<typeof getExecutionFeeParamsForRoute>): string {
  if (fees.capLamports == null) return "off";
  return `${(fees.estimatedMaxLamports / 1_000_000_000).toFixed(6)}/${(fees.capLamports / 1_000_000_000).toFixed(6)} SOL${fees.capped ? " capped" : ""}`;
}

export async function executeRustPumpSwapBuy(args: RustPumpSwapBuyArgs): Promise<RustPumpSwapBuyResult | null> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) return null;
  const id = `rust-pumpswap-${Date.now()}-${++executorSeq}`;
  const { blockhash } = await getCachedBlockhash();
  let quoteAmountInRaw: number;
  let expectedBaseOutRaw: number;
  let minBaseOutRaw: number;
  let slippageBps: number;
  try {
    const rawQuoteAmount = args.quoteAmountInRaw ?? args.solInLamports;
    if (rawQuoteAmount == null) throw new Error("quoteAmountInRaw must be provided");
    quoteAmountInRaw = integerCommandField(rawQuoteAmount, "quoteAmountInRaw", 1);
    expectedBaseOutRaw = integerCommandField(args.expectedBaseOutRaw, "expectedBaseOutRaw", 1);
    minBaseOutRaw = integerCommandField(args.minBaseOutRaw, "minBaseOutRaw", 1);
    slippageBps = integerCommandField(args.slippageBps, "slippageBps", 0);
  } catch (err) {
    return commandValidationFailure(err);
  }
  const fees = getExecutionFeeParamsForRoute(args.route, quoteAmountInRaw);
  const command = {
    kind: "executePumpSwapBuy",
    id,
    route: args.route,
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    buyLockOwner: args.buyLockOwner,
    quoteMint: args.quoteMint ?? "So11111111111111111111111111111111111111112",
    quoteAmountInRaw,
    expectedBaseOutRaw,
    minBaseOutRaw,
    slippageBps,
    buyInstruction: args.buyInstruction,
    sourcePumpSwapAccounts: args.sourcePumpSwapAccounts.map(normalizePumpSwapAccountMeta),
    baseTokenProgramId: args.baseTokenProgramId,
    preflight: pumpSwapRustPreflight,
    blockhash,
    computeUnitLimit,
    priorityFeeMicroLamports: fees.priorityFeeMicroLamports,
    jitoTipLamports: fees.jitoTipLamports,
    heliusTipLamports: fees.heliusTipLamports,
    useJitoDirect,
    useJitoFallback,
    senderRpcFallbackEnabled,
    executionMultiSendEnabled: executionMultiSendEnabled || broadcastRaceEnabled,
    jitoSendMode,
    jitoTransactionUrls,
    jitoBundleUrls,
    senderEndpointUrl,
    rpcFallbackUrl: readRpcUrl,
    jitoAuthUuid,
  };

  return await new Promise<RustPumpSwapBuyResult>((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `Rust PumpSwap executor timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    executorStdin!.write(`${JSON.stringify(command)}\n`);
  });
}

export async function executeRustPumpSwapSell(args: RustPumpSwapSellArgs): Promise<RustPumpSwapSellResult | null> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) return null;
  const id = `rust-pumpswap-sell-${Date.now()}-${++executorSeq}`;
  const { blockhash } = await getCachedBlockhash();
  let tokensInRaw: number;
  let expectedQuoteOutRaw: number;
  let minQuoteOutRaw: number;
  try {
    tokensInRaw = integerCommandField(args.tokensInRaw, "tokensInRaw", 1);
    expectedQuoteOutRaw = integerCommandField(args.expectedQuoteOutRaw, "expectedQuoteOutRaw", 1);
    minQuoteOutRaw = integerCommandField(args.minQuoteOutRaw, "minQuoteOutRaw", 1);
  } catch (err) {
    return commandValidationFailure(err);
  }
  const fees = getExecutionFeeParamsForRoute(args.route);
  const command = {
    kind: "executePumpSwapSell",
    id,
    route: args.route,
    mint: args.mint,
    tokensInRaw,
    expectedQuoteOutRaw,
    minQuoteOutRaw,
    sourcePumpSwapAccounts: args.sourcePumpSwapAccounts.map(normalizePumpSwapAccountMeta),
    baseTokenProgramId: args.baseTokenProgramId,
    blockhash,
    computeUnitLimit,
    priorityFeeMicroLamports: fees.priorityFeeMicroLamports,
    jitoTipLamports: fees.jitoTipLamports,
    heliusTipLamports: fees.heliusTipLamports,
    useJitoDirect,
    useJitoFallback,
    senderRpcFallbackEnabled,
    executionMultiSendEnabled: executionMultiSendEnabled || broadcastRaceEnabled,
    jitoSendMode,
    jitoTransactionUrls,
    jitoBundleUrls,
    senderEndpointUrl,
    rpcFallbackUrl: readRpcUrl,
    jitoAuthUuid,
  };

  return await new Promise<RustPumpSwapSellResult>((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `Rust PumpSwap sell executor timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    executorStdin!.write(`${JSON.stringify(command)}\n`);
  });
}

export async function dryBuildRustPumpFunSell(args: RustPumpFunSellArgs): Promise<RustDryBuildResult | null> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) return null;
  const id = `rust-dry-pumpfun-sell-${Date.now()}-${++executorSeq}`;
  const { blockhash } = await getCachedBlockhash();
  let tokensInRaw: number;
  let expectedSolOutLamports: number;
  let minSolOutLamports: number;
  try {
    tokensInRaw = integerCommandField(args.tokensInRaw, "tokensInRaw", 1);
    expectedSolOutLamports = integerCommandField(args.expectedSolOutLamports, "expectedSolOutLamports", 1);
    minSolOutLamports = integerCommandField(args.minSolOutLamports, "minSolOutLamports", 1);
  } catch (err) {
    return commandValidationFailure(err);
  }
  const fees = getExecutionFeeParamsForRoute(args.route);
  const command = {
    kind: "dryBuildPumpfunSell",
    id,
    route: args.route,
    mint: args.mint,
    tokensInRaw,
    expectedSolOutLamports,
    minSolOutLamports,
    sellV2: args.sellV2,
    accounts: args.accounts,
    blockhash,
    computeUnitLimit,
    priorityFeeMicroLamports: fees.priorityFeeMicroLamports,
    jitoTipLamports: fees.jitoTipLamports,
    heliusTipLamports: fees.heliusTipLamports,
    useJitoDirect,
    useJitoFallback,
    senderRpcFallbackEnabled,
    executionMultiSendEnabled: executionMultiSendEnabled || broadcastRaceEnabled,
    jitoSendMode,
    jitoTransactionUrls,
    jitoBundleUrls,
    senderEndpointUrl,
    rpcFallbackUrl: readRpcUrl,
    jitoAuthUuid,
  };

  return await new Promise<RustDryBuildResult>((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `Rust Pump.fun sell dry-build timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    executorStdin!.write(`${JSON.stringify(command)}\n`);
  });
}

export async function executeRustPumpFunSell(args: RustPumpFunSellArgs): Promise<RustPumpFunSellResult | null> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) return null;
  const id = `rust-pumpfun-sell-${Date.now()}-${++executorSeq}`;
  const { blockhash } = await getCachedBlockhash();
  let tokensInRaw: number;
  let expectedSolOutLamports: number;
  let minSolOutLamports: number;
  try {
    tokensInRaw = integerCommandField(args.tokensInRaw, "tokensInRaw", 1);
    expectedSolOutLamports = integerCommandField(args.expectedSolOutLamports, "expectedSolOutLamports", 1);
    minSolOutLamports = integerCommandField(args.minSolOutLamports, "minSolOutLamports", 1);
  } catch (err) {
    return commandValidationFailure(err);
  }
  const fees = getExecutionFeeParamsForRoute(args.route);
  const command = {
    kind: "executePumpfunSell",
    id,
    route: args.route,
    mint: args.mint,
    tokensInRaw,
    expectedSolOutLamports,
    minSolOutLamports,
    sellV2: args.sellV2,
    accounts: args.accounts,
    blockhash,
    computeUnitLimit,
    priorityFeeMicroLamports: fees.priorityFeeMicroLamports,
    jitoTipLamports: fees.jitoTipLamports,
    heliusTipLamports: fees.heliusTipLamports,
    useJitoDirect,
    useJitoFallback,
    senderRpcFallbackEnabled,
    executionMultiSendEnabled: executionMultiSendEnabled || broadcastRaceEnabled,
    jitoSendMode,
    jitoTransactionUrls,
    jitoBundleUrls,
    senderEndpointUrl,
    rpcFallbackUrl: readRpcUrl,
    jitoAuthUuid,
  };

  return await new Promise<RustPumpFunSellResult>((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `Rust Pump.fun sell executor timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    executorStdin!.write(`${JSON.stringify(command)}\n`);
  });
}

async function sendRustExitWatcherCommand(
  command: Record<string, unknown>,
  label: string
): Promise<{ ok: true; actionPending?: boolean; phase?: string } | { ok: false; error: string }> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) {
    return { ok: false, error: "Rust executor unavailable" };
  }
  const id = String(command.id);
  return await new Promise((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `${label} timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    executorStdin!.write(`${JSON.stringify(command)}\n`);
  });
}

export async function startRustPumpFunExitWatch(
  args: RustPumpFunExitWatchArgs
): Promise<{ ok: true } | { ok: false; error: string }> {
  const id = `rust-exit-watch-start-${Date.now()}-${++executorSeq}`;
  const fees = getExecutionFeeParamsForRoute(args.sellV2 ? "pumpfun-direct-sell-v2" : "pumpfun-direct-sell");
  const priorityLamports = Math.ceil(
    (fees.priorityFeeMicroLamports * Math.max(1, computeUnitLimit)) / 1_000_000
  );
  const estimatedExecutionFeeLamports =
    priorityLamports + Math.max(fees.jitoTipLamports, fees.heliusTipLamports);
  return await sendRustExitWatcherCommand(
    {
      kind: "startPumpfunExitWatch",
      id,
      mint: args.mint,
      sourceWallet: args.sourceWallet,
      bondingCurve: args.bondingCurve,
      entryTsMs: integerCommandField(args.entryTsMs, "entryTsMs", 0),
      tokensHeldRaw: integerCommandField(args.tokensHeldRaw, "tokensHeldRaw", 1),
      costBasisLamports: integerCommandField(args.costBasisLamports, "costBasisLamports", 1),
      buyConfirmed: args.buyConfirmed,
      markTelemetryMs: rustExitMarkTelemetryMs,
      thresholds: {
        stopLossPct: rustExitStopLossPct,
        stopLossGraceMs: rustExitStopLossGraceMs,
        takeProfitPct: rustExitTakeProfitPct,
        takeProfitSellPct: rustExitTakeProfitSellPct,
        earlySourceWindowMs: rustExitEarlySourceWindowMs,
        earlySourceMaxPnlPct: rustExitEarlySourceMaxPnlPct,
        sourcePartialSellPct: rustExitSourcePartialSellPct,
        runnerDrawdownPct: rustExitRunnerDrawdownPct,
        sellSlippageBps: Math.max(0, Math.min(10_000, mirrorSlippageBps)),
        estimatedExecutionFeeLamports,
      },
      sell: {
        sellV2: args.sellV2,
        accounts: args.accounts,
        computeUnitLimit,
        priorityFeeMicroLamports: fees.priorityFeeMicroLamports,
        jitoTipLamports: fees.jitoTipLamports,
        heliusTipLamports: fees.heliusTipLamports,
        useJitoDirect,
        useJitoFallback,
        senderRpcFallbackEnabled,
        executionMultiSendEnabled: executionMultiSendEnabled || broadcastRaceEnabled,
        jitoSendMode,
        jitoTransactionUrls,
        jitoBundleUrls,
        senderEndpointUrl,
        rpcFallbackUrl: readRpcUrl,
        jitoAuthUuid,
      },
      shadow: rustExitWatcherShadow,
    },
    "Rust exit watch registration"
  );
}

export async function updateRustPumpFunExitWatch(
  mint: string,
  tokensHeldRaw: number,
  costBasisLamports: number
): Promise<{ ok: true } | { ok: false; error: string }> {
  const id = `rust-exit-watch-update-${Date.now()}-${++executorSeq}`;
  return await sendRustExitWatcherCommand(
    {
      kind: "updatePumpfunExitWatch",
      id,
      mint,
      tokensHeldRaw: integerCommandField(tokensHeldRaw, "tokensHeldRaw", 1),
      costBasisLamports: integerCommandField(costBasisLamports, "costBasisLamports", 1),
    },
    "Rust exit watch update"
  );
}

export async function cancelRustPumpFunExitWatch(
  mint: string,
  reason: string
): Promise<{ ok: true } | { ok: false; error: string }> {
  const id = `rust-exit-watch-cancel-${Date.now()}-${++executorSeq}`;
  return await sendRustExitWatcherCommand(
    { kind: "cancelPumpfunExitWatch", id, mint, reason },
    "Rust exit watch cancellation"
  );
}

export async function markRustPumpFunExitSourceFlat(
  mint: string
): Promise<{ ok: true } | { ok: false; error: string }> {
  const id = `rust-exit-watch-flat-${Date.now()}-${++executorSeq}`;
  return await sendRustExitWatcherCommand(
    { kind: "sourceFlatPumpfunExitWatch", id, mint },
    "Rust exit watch source-flat signal"
  );
}

/**
 * Forward a source sell observed by any Node detector to the Rust watcher.
 * Rust also receives source sells from Yellowstone/ShredStream, so the
 * signature gate makes this handoff idempotent when both paths see the tx.
 */
export async function signalRustPumpFunExitSourceSell(args: {
  mint: string;
  sourceWallet: string;
  sourceSignature: string;
  soldTokensRaw?: number;
  sourceFlat?: boolean;
}): Promise<{ ok: true; actionPending: boolean } | { ok: false; error: string }> {
  const id = `rust-exit-watch-source-sell-${Date.now()}-${++executorSeq}`;
  const result = await sendRustExitWatcherCommand(
    {
      kind: "sourceSellPumpfunExitWatch",
      id,
      mint: args.mint,
      sourceWallet: args.sourceWallet,
      sourceSignature: args.sourceSignature,
      soldTokensRaw:
        args.soldTokensRaw == null
          ? undefined
          : integerCommandField(args.soldTokensRaw, "soldTokensRaw", 1),
      sourceFlat: args.sourceFlat === true,
    },
    "Rust exit watch source-sell signal"
  );
  return result.ok ? { ok: true, actionPending: result.actionPending === true } : result;
}

export async function activateRustPumpFunExitWatch(
  mint: string
): Promise<{ ok: true } | { ok: false; error: string }> {
  const id = `rust-exit-watch-activate-${Date.now()}-${++executorSeq}`;
  return await sendRustExitWatcherCommand(
    { kind: "activatePumpfunExitWatch", id, mint },
    "Rust exit watch activation"
  );
}

export async function ackRustPumpFunExitSell(args: {
  mint: string;
  sequence: number;
  ok: boolean;
  actualSolOutLamports?: number;
  error?: string;
}): Promise<{ ok: true } | { ok: false; error: string }> {
  const id = `rust-exit-watch-ack-${Date.now()}-${++executorSeq}`;
  return await sendRustExitWatcherCommand(
    {
      kind: "ackPumpfunExitSell",
      id,
      mint: args.mint,
      sequence: integerCommandField(args.sequence, "sequence", 1),
      ok: args.ok,
      actualSolOutLamports:
        args.actualSolOutLamports == null
          ? undefined
          : integerCommandField(args.actualSolOutLamports, "actualSolOutLamports", 0),
      error: args.error,
    },
    "Rust exit sell acknowledgment"
  );
}

export async function releaseRustBoughtMint(
  mint: string,
  reason: string,
  sourceSignature?: string
): Promise<{ ok: true } | { ok: false; error: string }> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) {
    return { ok: false, error: "Rust executor unavailable" };
  }
  const id = `rust-release-${Date.now()}-${++executorSeq}`;
  const command = {
    kind: "releaseBoughtMint",
    id,
    mint,
    reason,
    sourceSignature,
  };
  return await new Promise((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `Rust release timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    executorStdin!.write(`${JSON.stringify(command)}\n`);
  });
}

export async function executeRustRaydiumCpmmSwap(args: RustRaydiumCpmmSwapArgs): Promise<RustRaydiumCpmmSwapResult | null> {
  if (!rustExecutorEnabled || !executorStdin || (executorStdin as { destroyed?: boolean }).destroyed) return null;
  const id = `rust-raydium-cpmm-${Date.now()}-${++executorSeq}`;
  const { blockhash } = await getCachedBlockhash();
  let amountInRaw: number;
  let expectedOutRaw: number;
  let minOutRaw: number;
  try {
    amountInRaw = integerCommandField(args.amountInRaw, "amountInRaw", 1);
    expectedOutRaw = integerCommandField(args.expectedOutRaw, "expectedOutRaw", 1);
    minOutRaw = integerCommandField(args.minOutRaw, "minOutRaw", 1);
  } catch (err) {
    return commandValidationFailure(err);
  }
  const fees = getExecutionFeeParamsForRoute(args.route, args.side === "buy" ? amountInRaw : null);
  const command = {
    kind: "executeRaydiumCpmmSwap",
    id,
    route: args.route,
    side: args.side,
    mint: args.mint,
    sourceSignature: args.sourceSignature,
    inputMint: args.inputMint,
    outputMint: args.outputMint,
    amountInRaw,
    expectedOutRaw,
    minOutRaw,
    sourceRaydiumCpmmAccounts: args.sourceRaydiumCpmmAccounts.map(normalizePumpSwapAccountMeta),
    blockhash,
    computeUnitLimit,
    priorityFeeMicroLamports: fees.priorityFeeMicroLamports,
    jitoTipLamports: fees.jitoTipLamports,
    heliusTipLamports: fees.heliusTipLamports,
    useJitoDirect,
    useJitoFallback,
    senderRpcFallbackEnabled,
    executionMultiSendEnabled: executionMultiSendEnabled || broadcastRaceEnabled,
    jitoSendMode,
    jitoTransactionUrls,
    jitoBundleUrls,
    senderEndpointUrl,
    rpcFallbackUrl: readRpcUrl,
    jitoAuthUuid,
  };

  return await new Promise<RustRaydiumCpmmSwapResult>((resolve) => {
    const timer = setTimeout(() => {
      executorPending.delete(id);
      resolve({ ok: false, error: `Rust Raydium CPMM executor timed out after ${rustExecutorTimeoutMs}ms` });
    }, rustExecutorTimeoutMs);
    executorPending.set(id, { resolve, timer });
    executorStdin!.write(`${JSON.stringify(command)}\n`);
  });
}

