import { existsSync, readFileSync } from "node:fs";
import { executionTelemetryPath } from "./config.js";

type Row = {
  tsMs?: number;
  kind?: string;
  route?: string;
  side?: string;
  mint?: string;
  sourceSignature?: string;
  signature?: string;
  detector?: string;
  elapsedMs?: number;
  success?: boolean;
  errorClass?: string;
  errorCode?: string;
  errorProgram?: string;
  errorName?: string;
  errorSummary?: string;
  errorHint?: string;
  details?: Record<string, unknown>;
};

function key(row: Row, field: keyof Row, fallback = "unknown"): string {
  const value = row[field];
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function detailString(row: Row, field: string, fallback = "unknown"): string {
  const value = row.details?.[field];
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function detailRecord(row: Row, field: string): Record<string, unknown> | null {
  const value = row.details?.[field];
  return value != null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function pct(n: number, d: number): string {
  return d > 0 ? `${((n / d) * 100).toFixed(1)}%` : "0.0%";
}

function quantile(values: number[], q: number): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * q)));
  return sorted[idx]!;
}

function countBy(rows: Row[], getKey: (row: Row) => string): Array<[string, number]> {
  const counts = new Map<string, number>();
  for (const row of rows) counts.set(getKey(row), (counts.get(getKey(row)) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

function printTop(title: string, entries: Array<[string, number]>, total: number, limit = 12): void {
  console.log(`\n${title}`);
  for (const [name, count] of entries.slice(0, limit)) {
    console.log(`  ${String(count).padStart(4)}  ${pct(count, total).padStart(6)}  ${name}`);
  }
}

function fmtMs(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(0)}ms` : "-";
}

function printLatency(title: string, values: number[]): void {
  const xs = values.filter((value) => Number.isFinite(value));
  if (xs.length === 0) return;
  console.log(
    `${title}: n=${xs.length} p50=${fmtMs(quantile(xs, 0.5))} p90=${fmtMs(quantile(xs, 0.9))} p99=${fmtMs(quantile(xs, 0.99))}`
  );
}

function printNumberDistribution(title: string, values: number[], unit: string): void {
  const xs = values.filter((value) => Number.isFinite(value));
  if (xs.length === 0) return;
  console.log(
    `${title}: n=${xs.length} p50=${quantile(xs, 0.5).toFixed(0)}${unit} ` +
      `p90=${quantile(xs, 0.9).toFixed(0)}${unit} p99=${quantile(xs, 0.99).toFixed(0)}${unit}`
  );
}

function detailNumber(row: Row, field: string): number | null {
  const value = row.details?.[field];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

if (!existsSync(executionTelemetryPath)) {
  console.log(`No telemetry file found at ${executionTelemetryPath}`);
  process.exit(0);
}

const rows = readFileSync(executionTelemetryPath, "utf8")
  .split(/\r?\n/)
  .map((line) => line.trim())
  .filter(Boolean)
  .map((line): Row | null => {
    try {
      return JSON.parse(line) as Row;
    } catch {
      return null;
    }
  })
  .filter((row): row is Row => row != null);

const successes = rows.filter((row) => row.success === true);
const failures = rows.filter((row) => row.success === false);
const elapsed = rows
  .map((row) => row.elapsedMs)
  .filter((value): value is number => typeof value === "number" && Number.isFinite(value));

console.log("Execution Telemetry Report");
console.log(`File: ${executionTelemetryPath}`);
console.log(`Rows: ${rows.length}`);
console.log(`Success: ${successes.length} (${pct(successes.length, rows.length)})`);
console.log(`Failure: ${failures.length} (${pct(failures.length, rows.length)})`);
console.log(`Latency ms: p50=${quantile(elapsed, 0.5).toFixed(0)} p90=${quantile(elapsed, 0.9).toFixed(0)} p99=${quantile(elapsed, 0.99).toFixed(0)}`);

console.log("\nLatency by stage/kind");
for (const kind of [...new Set(rows.map((row) => row.kind).filter(Boolean))].sort()) {
  printLatency(`  ${kind}`, rows.filter((row) => row.kind === kind).map((row) => row.elapsedMs ?? NaN));
}

console.log("\nSource feed latency");
printLatency("  provider -> bot received", rows.map((row) => detailNumber(row, "providerToReceivedMs") ?? NaN));
printLatency("  bot received -> parsed", rows.map((row) => detailNumber(row, "receivedToDetectedMs") ?? NaN));
printLatency("  parsed -> process start", rows.map((row) => detailNumber(row, "detectedToProcessMs") ?? NaN));
printLatency("  provider -> process start", rows.map((row) => detailNumber(row, "providerToProcessMs") ?? NaN));

const chainRows = rows
  .filter((row) => row.sourceSignature)
  .reduce((map, row) => {
    const sig = row.sourceSignature!;
    const group = map.get(sig) ?? [];
    group.push(row);
    map.set(sig, group);
    return map;
  }, new Map<string, Row[]>());

type Chain = {
  sig: string;
  mint: string;
  detector: string;
  sourceSlot: number | null;
  sourceTransactionIndex: number | null;
  landingSlot: number | null;
  slotDelta: number | null;
  seenToBroadcastMs: number | null;
  processToBroadcastMs: number | null;
  broadcastToConfirmMs: number | null;
  providerToConfirmMs: number | null;
};

const chains: Chain[] = [];
for (const [sig, group] of chainRows) {
  const sorted = [...group].sort((a, b) => (a.tsMs ?? 0) - (b.tsMs ?? 0));
  const source = sorted.find((row) => row.kind === "source-detected");
  const broadcast = sorted.find((row) => row.kind === "broadcast" && row.success === true);
  const confirm = sorted.find((row) => row.kind === "stage:confirm");
  if (!source && !broadcast && !confirm) continue;
  const providerCreatedAt = detailNumber(source ?? sorted[0]!, "providerCreatedAtMs");
  const processStartedAt = detailNumber(source ?? sorted[0]!, "processStartedAtMs");
  const sourceSlot = detailNumber(source ?? sorted[0]!, "sourceSlot");
  const sourceTransactionIndex = detailNumber(source ?? sorted[0]!, "sourceTransactionIndex");
  const landingSlot = detailNumber(confirm ?? sorted[0]!, "landingSlot");
  const broadcastAt = broadcast?.tsMs ?? null;
  const confirmAt = confirm?.tsMs ?? null;
  chains.push({
    sig,
    mint: (source?.mint ?? broadcast?.mint ?? confirm?.mint ?? "unknown").slice(0, 8),
    detector: String((source?.details?.detector ?? source?.detector ?? broadcast?.detector ?? "unknown")),
    sourceSlot,
    sourceTransactionIndex,
    landingSlot,
    slotDelta: sourceSlot != null && landingSlot != null ? landingSlot - sourceSlot : null,
    seenToBroadcastMs:
      providerCreatedAt != null && broadcastAt != null ? broadcastAt - providerCreatedAt : null,
    processToBroadcastMs:
      processStartedAt != null && broadcastAt != null ? broadcastAt - processStartedAt : null,
    broadcastToConfirmMs:
      broadcastAt != null && confirmAt != null ? confirmAt - broadcastAt : null,
    providerToConfirmMs:
      providerCreatedAt != null && confirmAt != null ? confirmAt - providerCreatedAt : null,
  });
}

if (chains.length > 0) {
  console.log("\nEnd-to-end chains");
  printLatency("  provider -> broadcast", chains.map((chain) => chain.seenToBroadcastMs ?? NaN));
  printLatency("  process -> broadcast", chains.map((chain) => chain.processToBroadcastMs ?? NaN));
  printLatency("  broadcast -> confirm", chains.map((chain) => chain.broadcastToConfirmMs ?? NaN));
  printLatency("  provider -> confirm", chains.map((chain) => chain.providerToConfirmMs ?? NaN));
  printNumberDistribution("  source slot -> landing slot", chains.map((chain) => chain.slotDelta ?? NaN), " slot(s)");
  console.log("\nLatest chains");
  for (const chain of chains.slice(-10).reverse()) {
    const slotInfo =
      chain.sourceSlot == null && chain.landingSlot == null
        ? ""
        : ` sourceSlot=${chain.sourceSlot ?? "-"} txIdx=${chain.sourceTransactionIndex ?? "-"} landingSlot=${chain.landingSlot ?? "-"} delta=${chain.slotDelta ?? "-"} `;
    console.log(
      `  ${chain.sig.slice(0, 8)} ${chain.mint} ${chain.detector}: ` +
        slotInfo +
        `provider->broadcast=${fmtMs(chain.seenToBroadcastMs)} ` +
        `process->broadcast=${fmtMs(chain.processToBroadcastMs)} ` +
        `broadcast->confirm=${fmtMs(chain.broadcastToConfirmMs)}`
    );
  }
}

printTop("By kind", countBy(rows, (row) => key(row, "kind")), rows.length);
printTop("By route", countBy(rows, (row) => key(row, "route")), rows.length);
printTop("By account plan", countBy(rows, (row) => detailString(row, "accountPlan")), rows.length);
printTop("By compact mode", countBy(rows, (row) => detailString(row, "compactMode")), rows.length);
printTop("By tip set", countBy(rows, (row) => detailString(row, "tipSet")), rows.length);
printTop("Failures by class", countBy(failures, (row) => key(row, "errorClass")), Math.max(1, failures.length));
printTop("Failures by error name", countBy(failures, (row) => key(row, "errorName")), Math.max(1, failures.length));
printTop("Failures by program", countBy(failures, (row) => key(row, "errorProgram")), Math.max(1, failures.length));
printTop("Failures by route", countBy(failures, (row) => key(row, "route")), Math.max(1, failures.length));
printTop("Failures by stage/kind", countBy(failures, (row) => key(row, "kind")), Math.max(1, failures.length));
printTop("Failures by account plan", countBy(failures, (row) => detailString(row, "accountPlan")), Math.max(1, failures.length));

const accountDiagnostics = rows
  .map((row) => ({ row, diagnostics: detailRecord(row, "accountDiagnostics") }))
  .filter((entry): entry is { row: Row; diagnostics: Record<string, unknown> } => entry.diagnostics != null);
if (accountDiagnostics.length > 0) {
  const mismatches = accountDiagnostics.filter(({ diagnostics }) => {
    const count = diagnostics.pubkeyMismatchCount;
    return typeof count === "number" && count > 0;
  });
  console.log("\nPumpSwap account diagnostics");
  console.log(`  Rows with diagnostics: ${accountDiagnostics.length}`);
  console.log(`  Detector/canonical pubkey mismatches: ${mismatches.length}`);

  for (const { row, diagnostics } of mismatches.slice(-8).reverse()) {
    const mint = row.mint ? `${row.mint.slice(0, 8)}...` : "unknown";
    const count = typeof diagnostics.pubkeyMismatchCount === "number" ? diagnostics.pubkeyMismatchCount : 0;
    const indexes = Array.isArray(diagnostics.pubkeyMismatchIndexes)
      ? diagnostics.pubkeyMismatchIndexes.slice(0, 8).join(",")
      : "";
    console.log(`  ${row.kind ?? "event"} ${mint}: ${count} pubkey mismatch(es) at [${indexes}]`);
  }
}

const latestFailures = failures.slice(-10).reverse();
if (latestFailures.length > 0) {
  console.log("\nLatest failures");
  for (const row of latestFailures) {
    const mint = row.mint ? `${row.mint.slice(0, 8)}...` : "unknown";
    const cls = row.errorClass ?? "unknown";
    const code = row.errorCode ? `/${row.errorCode}` : "";
    const name = row.errorName ? `/${row.errorName}` : "";
    const hint = row.errorHint ? ` | ${row.errorHint}` : "";
    console.log(`  ${row.kind ?? "event"} ${row.route ?? "unknown"} ${mint}: ${cls}${code}${name}${hint}`);
  }
}
