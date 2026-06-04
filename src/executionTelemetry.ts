import { mkdir, appendFile } from "node:fs/promises";
import { dirname } from "node:path";
import { executionTelemetryEnabled, executionTelemetryPath } from "./config.js";
import { classifyExecutionError } from "./errorClassifier.js";

export type ExecutionTelemetryEvent = {
  ts?: string;
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
    const row = {
      ts: event.ts ?? new Date().toISOString(),
      ...event,
      errorClass: event.errorClass ?? classified?.category,
      errorCode: event.errorCode ?? classified?.code,
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
  stage: "quote" | "build" | "preflight" | "broadcast" | "confirm";
}): void {
  recordExecutionTelemetrySync({
    ...args,
    kind: `stage:${args.stage}`,
  });
}
