const MAX_ENTRIES = 500;

export type LogEntry = {
  ts: number;
  level: "log" | "warn" | "error";
  message: string;
};

const buffer: LogEntry[] = [];
let installed = false;

function stringify(value: unknown): string {
  if (typeof value === "string") return value;
  if (value instanceof Error) return value.stack ?? value.message;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function push(level: LogEntry["level"], args: unknown[]): void {
  const message = args.map(stringify).join(" ");
  buffer.push({ ts: Date.now(), level, message });
  if (buffer.length > MAX_ENTRIES) buffer.shift();
}

export function installConsolePatch(): void {
  if (installed) return;
  installed = true;

  const origLog = console.log.bind(console);
  const origWarn = console.warn.bind(console);
  const origError = console.error.bind(console);

  console.log = (...args: unknown[]) => {
    push("log", args);
    origLog(...args);
  };
  console.warn = (...args: unknown[]) => {
    push("warn", args);
    origWarn(...args);
  };
  console.error = (...args: unknown[]) => {
    push("error", args);
    origError(...args);
  };
}

export function getLogs(sinceTs: number = 0): LogEntry[] {
  if (sinceTs <= 0) return buffer.slice();
  return buffer.filter((e) => e.ts > sinceTs);
}
