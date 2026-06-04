import { existsSync, readFileSync } from "node:fs";
import { executionTelemetryPath } from "./config.js";

type Row = {
  kind?: string;
  route?: string;
  side?: string;
  mint?: string;
  sourceSignature?: string;
  signature?: string;
  elapsedMs?: number;
  success?: boolean;
  errorClass?: string;
  errorCode?: string;
  errorSummary?: string;
  errorHint?: string;
  details?: Record<string, unknown>;
};

function key(row: Row, field: keyof Row, fallback = "unknown"): string {
  const value = row[field];
  return typeof value === "string" && value.length > 0 ? value : fallback;
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

printTop("By kind", countBy(rows, (row) => key(row, "kind")), rows.length);
printTop("By route", countBy(rows, (row) => key(row, "route")), rows.length);
printTop("Failures by class", countBy(failures, (row) => key(row, "errorClass")), Math.max(1, failures.length));
printTop("Failures by route", countBy(failures, (row) => key(row, "route")), Math.max(1, failures.length));
printTop("Failures by stage/kind", countBy(failures, (row) => key(row, "kind")), Math.max(1, failures.length));

const latestFailures = failures.slice(-10).reverse();
if (latestFailures.length > 0) {
  console.log("\nLatest failures");
  for (const row of latestFailures) {
    const mint = row.mint ? `${row.mint.slice(0, 8)}...` : "unknown";
    const cls = row.errorClass ?? "unknown";
    const code = row.errorCode ? `/${row.errorCode}` : "";
    const hint = row.errorHint ? ` | ${row.errorHint}` : "";
    console.log(`  ${row.kind ?? "event"} ${row.route ?? "unknown"} ${mint}: ${cls}${code}${hint}`);
  }
}

