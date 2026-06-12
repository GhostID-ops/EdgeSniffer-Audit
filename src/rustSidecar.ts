import { spawn, type ChildProcess } from "node:child_process";
import { createInterface } from "node:readline";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { rustSidecarBin, rustSidecarUseCargo } from "./config.js";
import type { DetectedSwap } from "./types.js";
import type { SignatureDedupe } from "./watcher.js";

type RustSidecarEvent = {
  kind?: string;
  source?: string;
  providerCreatedAtMs?: number;
  receivedAtMs?: number;
  detectedAtMs?: number;
  signature?: string;
  wallet?: string;
  inputMint?: string;
  outputMint?: string;
  amountRaw?: number;
  isNativeInput?: boolean;
  counterpartyAmountRaw?: number;
  sourceIxAccounts?: string[];
  sourceIxDataBase64?: string;
  sourcePumpSwapAccounts?: string[];
  sourceQuoteMint?: string;
  sourceQuoteAmountRaw?: number;
};

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
    inputMint: event.inputMint,
    outputMint: event.outputMint,
    amountRaw: event.amountRaw,
    isNativeInput: event.isNativeInput,
    counterpartyAmountRaw:
      typeof event.counterpartyAmountRaw === "number" ? event.counterpartyAmountRaw : undefined,
    sourceIxAccounts: Array.isArray(event.sourceIxAccounts) ? event.sourceIxAccounts : undefined,
    sourceIxDataBase64:
      typeof event.sourceIxDataBase64 === "string" ? event.sourceIxDataBase64 : undefined,
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
    return {
      command: "cargo",
      args: ["run", "--manifest-path", "rust-sidecar/Cargo.toml", "--release"],
      label: "cargo run --manifest-path rust-sidecar/Cargo.toml --release",
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
  onSwap: (swap: DetectedSwap) => void
): Promise<RustSidecarUnsubscribe> {
  const jitoShredstreamEnabled = envFlag("JITO_SHREDSTREAM_ENABLED");
  const jitoShredstreamGrpcUrl = process.env.JITO_SHREDSTREAM_GRPC_URL ?? "http://127.0.0.1:9999";
  const yellowstoneEnabled = envFlag("RUST_SIDECAR_YELLOWSTONE_ENABLED", true);
  console.log(
    `Rust sidecar config: Yellowstone=${yellowstoneEnabled ? "ON" : "OFF"}, ` +
      `Jito ShredStream=${jitoShredstreamEnabled ? "ON" : "OFF"}` +
      (jitoShredstreamEnabled ? ` (${jitoShredstreamGrpcUrl})` : "")
  );

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
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let proc: ChildProcess | null = child;

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

    const swap = toSwap(event, nodeReceivedAtMs);
    if (!swap) return;
    if (processedSignatures.has(swap.signature)) return;
    processedSignatures.add(swap.signature);

    const ageMs =
      typeof event.detectedAtMs === "number" && Number.isFinite(event.detectedAtMs)
        ? nodeReceivedAtMs - event.detectedAtMs
        : null;
    console.log(
      `Rust sidecar detected ${swap.inputMint === "So11111111111111111111111111111111111111112" ? "buy" : "sell"} ` +
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
    if (proc) {
      console.warn(`Rust sidecar exited (${signal ?? code ?? "unknown"}).`);
    }
  });

  return async () => {
    const running = proc;
    proc = null;
    stdout.close();
    stderr.close();
    if (running && !running.killed) {
      running.kill();
    }
  };
}
