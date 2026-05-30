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
  detectedAtMs?: number;
  signature?: string;
  wallet?: string;
  inputMint?: string;
  outputMint?: string;
  amountRaw?: number;
  isNativeInput?: boolean;
  counterpartyAmountRaw?: number;
  sourceIxAccounts?: string[];
  sourcePumpSwapAccounts?: string[];
  sourceQuoteMint?: string;
  sourceQuoteAmountRaw?: number;
};

export type RustSidecarUnsubscribe = () => Promise<void>;

function toSwap(event: RustSidecarEvent): DetectedSwap | null {
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
    inputMint: event.inputMint,
    outputMint: event.outputMint,
    amountRaw: event.amountRaw,
    isNativeInput: event.isNativeInput,
    counterpartyAmountRaw:
      typeof event.counterpartyAmountRaw === "number" ? event.counterpartyAmountRaw : undefined,
    sourceIxAccounts: Array.isArray(event.sourceIxAccounts) ? event.sourceIxAccounts : undefined,
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
  const command = commandForSidecar();
  if (!command) return async () => {};

  const child = spawn(command.command, command.args, {
    cwd: process.cwd(),
    env: process.env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  });
  let proc: ChildProcess | null = child;

  console.log(`Rust sidecar: started ${command.label}`);

  if (!child.stdout || !child.stderr) {
    console.warn("Rust sidecar: stdout/stderr unavailable; sidecar disabled.");
    child.kill();
    return async () => {};
  }

  const stdout = createInterface({ input: child.stdout });
  const stderr = createInterface({ input: child.stderr });

  stdout.on("line", (line) => {
    if (!line.trim()) return;
    let event: RustSidecarEvent;
    try {
      event = JSON.parse(line) as RustSidecarEvent;
    } catch {
      console.log(`Rust sidecar: ${line}`);
      return;
    }

    const swap = toSwap(event);
    if (!swap) return;
    if (processedSignatures.has(swap.signature)) return;
    processedSignatures.add(swap.signature);

    const ageMs =
      typeof event.detectedAtMs === "number" && Number.isFinite(event.detectedAtMs)
        ? Date.now() - event.detectedAtMs
        : null;
    console.log(
      `Rust sidecar detected ${swap.inputMint === "So11111111111111111111111111111111111111112" ? "buy" : "sell"} ` +
        `${swap.signature.slice(0, 8)}...` +
        (ageMs != null ? ` (handoff ${ageMs}ms)` : "")
    );
    onSwap(swap);
  });

  stderr.on("line", (line) => {
    if (line.trim()) console.log(line);
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
