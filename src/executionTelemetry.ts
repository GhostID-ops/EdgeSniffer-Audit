import { mkdir, appendFile } from "node:fs/promises";
import { dirname } from "node:path";
import { executionTelemetryEnabled, executionTelemetryPath } from "./config.js";
import { classifyExecutionError } from "./errorClassifier.js";
import { getLastConfirmationResult } from "./confirm.js";

export type ExecutionTelemetryEvent = {
  ts?: string;
  tsMs?: number;
  kind: string;
  route?: string;
  side?: "buy" | "sell" | "swap";
  mint?: string;
  sourceSignature?: string;
  signature?: string;
  wallet?: string;
  detector?: string;
  accountPlan?: string;
  elapsedMs?: number;
  buildMs?: number;
  broadcastMs?: number;
  preflightMs?: number;
  confirmationMs?: number;
  success?: boolean;
  error?: string;
  errorClass?: string;
  errorCode?: string;
  errorProgram?: string;
  errorName?: string;
  errorSummary?: string;
  errorHint?: string;
  recoverable?: boolean;
  details?: Record<string, unknown>;
};

let telemetryDirReady = false;

export async function recordExecutionTelemetry(event: ExecutionTelemetryEvent): Promise<void> {
  if (!executionTelemetryEnabled) return;
  try {
    if (!telemetryDirReady) {
      await mkdir(dirname(executionTelemetryPath), { recursive: true });
      telemetryDirReady = true;
    }
    const classified = event.error ? classifyExecutionError(event.error) : null;
    const tsMs = event.tsMs ?? Date.now();
    const confirmation =
      event.kind === "stage:confirm" && event.signature
        ? getLastConfirmationResult(event.signature)
        : null;
    const details =
      confirmation == null
        ? event.details
        : {
            ...(event.details ?? {}),
            landingSlot: confirmation.slot,
            confirmationStatus: confirmation.confirmationStatus,
          };
    const row = {
      ts: event.ts ?? new Date(tsMs).toISOString(),
      tsMs,
      ...event,
      details,
      errorClass: event.errorClass ?? classified?.category,
      errorCode: event.errorCode ?? classified?.code,
      errorProgram: event.errorProgram ?? classified?.program,
      errorName: event.errorName ?? classified?.errorName,
      errorSummary: event.errorSummary ?? classified?.summary,
      errorHint: event.errorHint ?? classified?.hint,
      recoverable: event.recoverable ?? classified?.recoverable,
    };
    await appendFile(executionTelemetryPath, `${JSON.stringify(row)}\n`, "utf8");
  } catch {
    // Telemetry must never block trading.
  }
}

export function recordExecutionTelemetrySync(event: ExecutionTelemetryEvent): void {
  void recordExecutionTelemetry(event);
}

export function recordExecutionStage(args: Omit<ExecutionTelemetryEvent, "kind"> & {
  stage: "detect" | "plan" | "quote" | "swap-response" | "build" | "preflight" | "broadcast" | "confirm" | "metadata" | "cleanup";
}): void {
  recordExecutionTelemetrySync({
    ...args,
    kind: `stage:${args.stage}`,
  });
}
